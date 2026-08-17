// useProtectedApi (M6): the "a 401 on a protected call sends you back to
// login" pattern, in one place so every future protected screen (macro
// catalog, execution runner, ...) reuses it instead of each re-implementing
// its own try/catch around UnauthorizedError.
//
// Usage: const callProtected = useProtectedApi();
//        const macros = await callProtected(() => listMacros(session.token));
// If the wrapped call throws UnauthorizedError, this calls logout() (which
// clears the session and, via App.jsx's session-based routing, bounces the
// user back to LoginPage) and rethrows so the caller's own error handling
// (a loading spinner, an error banner, etc.) still runs.

import { useCallback } from "react";
import { useAuth } from "./AuthContext";
import { UnauthorizedError } from "../api/client";

export function useProtectedApi() {
  const { logout } = useAuth();

  return useCallback(
    async (apiCall) => {
      try {
        return await apiCall();
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          logout();
        }
        throw err;
      }
    },
    [logout]
  );
}
