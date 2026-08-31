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

// Thrown by buildMacro's 422 case when the submitted source isn't valid
// Python at all -- main.py's handle_macro_syntax_error returns
// {error: "syntax_error", message, line, source_line} directly as the
// response body (not wrapped in `detail`, since that response comes from
// a FastAPI exception handler, not an HTTPException). Carries line/message
// separately so MacroForm can render the "syntax error on line N" panel
// instead of a generic error string.
export class MacroSyntaxError extends Error {
  constructor(message, line, sourceLine) {
    super(message);
    this.name = "MacroSyntaxError";
    this.line = line;
    this.sourceLine = sourceLine;
  }
}

// Thrown by buildMacro's 422 case when the image build itself failed
// (docker build / k3d image import exiting non-zero -- e.g. a
// requirements.txt package that doesn't exist) -- distinct from
// MacroSyntaxError so MacroForm can render the plain error panel instead
// of the line-highlighting one.
export class BuildFailedError extends Error {
  constructor(message) {
    super(message);
    this.name = "BuildFailedError";
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

// Decodes a JWT's payload without verifying its signature -- reading
// claims client-side is fine because the backend is the actual authority
// on validity (every protected request re-verifies the token there); the
// frontend only ever uses these claims for UI decisions (show/hide the
// Admin nav item, guard the admin page), never as an access-control
// mechanism itself.
function decodeJwtPayload(token) {
  const payloadSegment = token.split(".")[1];
  const base64 = payloadSegment.replace(/-/g, "+").replace(/_/g, "/");
  return JSON.parse(atob(base64));
}

// M7: decodes user_id/role out of the JWT alongside the existing
// username, so AuthContext can store them once in session -- see
// docs/decisions/013-per-user-accounts.md for the payload shape
// ({sub, user_id, role, exp}).
export async function login(username, password) {
  const response = await request("/auth/login", {
    method: "POST",
    body: { username, password },
    treatUnauthorizedAsSessionExpiry: false,
  });
  const body = await response.json();
  const payload = decodeJwtPayload(body.access_token);
  return { ...body, userId: payload.user_id, role: payload.role };
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
//
// Doesn't go through request()'s generic error handling for the 422
// case: two different failure shapes both use 422, and they need to be
// told apart to render different UI (see MacroSyntaxError/BuildFailedError
// above). A syntax error comes from main.py's exception handler
// (handle_macro_syntax_error) as a bare JSONResponse -- body IS
// {error, message, line, source_line}, no `detail` wrapper, since that's
// not an HTTPException. A build failure comes from an HTTPException with
// a structured `detail` ({error: "build_failed", message}), same wrapping
// pattern as uploadInput's ValidationError. Every other non-2xx (401, 400
// invalid icon) still goes through the shared 401/error handling.
export async function buildMacro(token, technicalName, { displayName, description, icon, sourceCode }) {
  const url = `${API_BASE_URL}/macros/${encodeURIComponent(technicalName)}/build`;
  const response = await fetch(url, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      display_name: displayName,
      description,
      icon,
      source_code: sourceCode,
    }),
  });

  if (response.status === 401) {
    throw new UnauthorizedError();
  }

  if (response.status === 422) {
    const body = await response.json().catch(() => null);
    if (body?.error === "syntax_error") {
      throw new MacroSyntaxError(body.message, body.line, body.source_line);
    }
    if (body?.detail?.error === "build_failed") {
      throw new BuildFailedError(body.detail.message);
    }
    throw new Error(body?.detail || body?.message || "build failed");
  }

  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new Error(detail?.detail || `request failed with status ${response.status}`);
  }

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

// Powers the History page (M6, continued) -- GET /executions returns every
// recorded execution from the backend's SQLite executions table, most
// recently created first, independent of whether the underlying Kubernetes
// Job still exists (see docs/decisions/006-execution-history.md).
export async function listExecutions(token) {
  const response = await request("/executions", { token });
  return response.json();
}

// Returns a Blob, not JSON -- GET /executions/{job_name}/result streams the
// raw output CSV (text/csv), so this bypasses request()'s .json() call and
// reads the body as a blob instead, ready to hand to a download link.
export async function downloadResult(token, jobName) {
  const response = await request(`/executions/${encodeURIComponent(jobName)}/result`, { token });
  return response.blob();
}

// User management (M7, admin-only endpoints -- see
// docs/decisions/013-per-user-accounts.md). All four go through
// request()'s shared 401 handling; POST/PUT's caller-mistake status codes
// (409 duplicate username, 422 invalid role, 400 last-admin guard) carry a
// plain-string `detail`, so the generic error path's Error(detail?.detail)
// already renders correctly -- no dedicated error class needed here, unlike
// buildMacro/uploadInput's structured-detail cases.
export async function listUsers(token) {
  const response = await request("/users", { token });
  return response.json();
}

export async function createUser(token, { username, password, role }) {
  const response = await request("/users", {
    method: "POST",
    token,
    body: { username, password, role },
  });
  return response.json();
}

export async function updateUser(token, userId, { role, password }) {
  const response = await request(`/users/${encodeURIComponent(userId)}`, {
    method: "PUT",
    token,
    body: { role, password },
  });
  return response.json();
}

export async function deleteUser(token, userId) {
  const response = await request(`/users/${encodeURIComponent(userId)}`, {
    method: "DELETE",
    token,
  });
  return response.json();
}
