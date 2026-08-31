// Auth context (M6, extended M7): holds the session (token + username +
// role) in memory only.
//
// Deliberately not localStorage/sessionStorage -- a page refresh logging
// the user out is an accepted tradeoff at this stage (see CLAUDE.md's M6
// scope), not an oversight. Any component can call useAuth() to read the
// current session, log in, or log out; a UnauthorizedError from api/client.js
// (thrown when a protected call's token is no longer valid) is the other
// path into the same logout() call, so an expired session and a manual
// logout both land the user on the login page the same way.
//
// M7: role/userId come from api/client.js's login(), which already
// decodes them out of the JWT payload once -- stored here so every
// consumer (Sidebar's Admin nav item, App.jsx's admin-route guard) reads
// session.role instead of each re-decoding the token itself.

import { createContext, useContext, useState, useCallback } from "react";
import { login as loginRequest } from "../api/client";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [session, setSession] = useState(null); // { token, username, userId, role } | null

  const login = useCallback(async (username, password) => {
    const { access_token, userId, role } = await loginRequest(username, password);
    setSession({ token: access_token, username, userId, role });
  }, []);

  const logout = useCallback(() => {
    setSession(null);
  }, []);

  return (
    <AuthContext.Provider value={{ session, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside an AuthProvider");
  return ctx;
}
