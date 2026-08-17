// API client (M6): thin fetch wrapper for backend-api.
//
// Two responsibilities beyond a plain fetch(): attach the bearer token to
// every request that needs one, and centralize the "the token is no longer
// valid" signal (a 401 response) into a single UnauthorizedError that the
// rest of the app can catch in one place, rather than every call site
// checking response.status itself.

const API_BASE_URL = "http://localhost:8000";

export class UnauthorizedError extends Error {
  constructor() {
    super("session expired or invalid -- please log in again");
    this.name = "UnauthorizedError";
  }
}

// Thrown specifically by uploadInput's 422 case, where the backend's
// `detail` is a structured object ({missing_columns, detected_headers}),
// not a plain string message -- request()'s generic error path only ever
// stringifies `detail` into Error.message, which would collapse this into
// an unreadable "[object Object]". Callers that need the specific missing
// column names catch this type instead of parsing err.message.
export class ValidationError extends Error {
  constructor(missingColumns, detectedHeaders) {
    super(`missing required columns: ${missingColumns.join(", ")}`);
    this.name = "ValidationError";
    this.missingColumns = missingColumns;
    this.detectedHeaders = detectedHeaders;
  }
}

async function request(
  path,
  { method = "GET", token, body, isFormData = false, treatUnauthorizedAsSessionExpiry = true } = {}
) {
  const headers = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (body && !isFormData) headers["Content-Type"] = "application/json";

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    body: isFormData ? body : body ? JSON.stringify(body) : undefined,
  });

  // A 401 means two different things depending on which endpoint sent it:
  // on /auth/login it means "wrong username or password" (a form error to
  // show inline). On every *protected* endpoint it means "this token is no
  // longer valid" (a session that should bounce back to the login page).
  // treatUnauthorizedAsSessionExpiry tells the caller which case this is,
  // since the status code alone can't.
  if (response.status === 401 && treatUnauthorizedAsSessionExpiry) {
    throw new UnauthorizedError();
  }

  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new Error(detail?.detail || `request failed with status ${response.status}`);
  }

  return response;
}

export async function login(username, password) {
  const response = await request("/auth/login", {
    method: "POST",
    body: { username, password },
    treatUnauthorizedAsSessionExpiry: false,
  });
  return response.json();
}

// First protected call wired up, ahead of the catalog screen itself: proves
// the UnauthorizedError -> logout()/redirect-to-login pattern (see
// auth/useProtectedApi.js) actually fires end to end, not just in theory.
export async function listMacros(token) {
  const response = await request("/macros", { token });
  return response.json();
}

// JSON body -- matches POST /macros/{technical_name}/build's contract as
// of M6's SQLite registry (main.py's BuildMacroRequest): display_name,
// description, icon, and source_code all travel together now, not just
// the raw source as a plain-text body like before the registry existed.
export async function buildMacro(token, technicalName, { displayName, description, icon, sourceCode }) {
  const response = await request(`/macros/${encodeURIComponent(technicalName)}/build`, {
    method: "POST",
    token,
    body: {
      display_name: displayName,
      description,
      icon,
      source_code: sourceCode,
    },
  });
  return response.json();
}

// One entry's full record (including source_code) -- used to pre-fill
// EditMacroForm, not by the catalog listing itself, which only needs
// listMacros().
export async function getMacro(token, technicalName) {
  const response = await request(`/macros/${encodeURIComponent(technicalName)}`, { token });
  return response.json();
}

export async function deleteMacro(token, technicalName) {
  const response = await request(`/macros/${encodeURIComponent(technicalName)}`, {
    method: "DELETE",
    token,
  });
  return response.json();
}

// FormData body -- deliberately no explicit Content-Type header. The
// browser sets one itself (multipart/form-data with the correct boundary
// string), which request()'s isFormData flag already accounts for; setting
// it manually here would omit that boundary and the backend's UploadFile
// parsing would fail to split the multipart body correctly.
//
// Doesn't go through request()'s generic error handling for the 422 case:
// a missing-columns failure carries a structured `detail`
// ({missing_columns, detected_headers}), not a plain string, so it's
// parsed here and re-thrown as ValidationError with both fields intact.
// Every other non-2xx (401, 404 unbuilt macro, 422 unparseable file with a
// plain-string detail) still goes through the shared 401/error handling.
export async function uploadInput(token, technicalName, file) {
  const formData = new FormData();
  formData.append("file", file);

  const url = `${API_BASE_URL}/macros/${encodeURIComponent(technicalName)}/input`;
  const response = await fetch(url, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: formData,
  });

  if (response.status === 401) {
    throw new UnauthorizedError();
  }

  if (response.status === 422) {
    const body = await response.json().catch(() => null);
    if (body?.detail && typeof body.detail === "object") {
      throw new ValidationError(body.detail.missing_columns, body.detail.detected_headers);
    }
    throw new Error(body?.detail || "the uploaded file could not be validated");
  }

  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new Error(detail?.detail || `request failed with status ${response.status}`);
  }

  return response.json();
}

export async function runMacro(token, technicalName) {
  const response = await request(`/executions/${encodeURIComponent(technicalName)}`, {
    method: "POST",
    token,
  });
  return response.json();
}

export async function getExecutionStatus(token, jobName) {
  const response = await request(`/executions/${encodeURIComponent(jobName)}`, { token });
  return response.json();
}

// Returns a Blob, not JSON -- GET /executions/{job_name}/result streams the
// raw output CSV (text/csv), so this bypasses request()'s .json() call and
// reads the body as a blob instead, ready to hand to a download link.
export async function downloadResult(token, jobName) {
  const response = await request(`/executions/${encodeURIComponent(jobName)}/result`, { token });
  return response.blob();
}
