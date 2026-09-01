// Auth context (M6, extended M7): holds the session (token + username +
// role), persisted to sessionStorage so a page refresh doesn't log the
// user out -- the earlier in-memory-only choice was an accepted M6-scope
// tradeoff, not a permanent design decision; a colleague hitting refresh
// and landing back on the login screen is real friction with a logout
// button already available for the case where signing out is actually
// wanted. sessionStorage (not localStorage): the session still ends when
// the tab/browser closes, rather than persisting indefinitely on a shared
// machine.
//
// Any component can call useAuth() to read the current session, log in,
// or log out; a UnauthorizedError from api/client.js (thrown when a
// protected call's token is no longer valid) is the other path into the
// same logout() call, so an expired session and a manual logout both land
// the user on the login page the same way.
//
// M7: role/userId come from api/client.js's login(), which already
// decodes them out of the JWT payload once -- stored here so every
// consumer (Sidebar's Admin nav item, App.jsx's admin-route guard) reads
// session.role instead of each re-decoding the token itself.

import { createContext, useContext, useState, useCallback } from "react";
import { login as loginRequest } from "../api/client";

const AuthContext = createContext(null);
const STORAGE_KEY = "radio-maas-session";

function readStoredSession() {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    // Corrupt/unparseable stored value, or sessionStorage unavailable
    // (e.g. a private-browsing mode that blocks it) -- fall back to a
    // fresh login rather than crashing the app on startup.
    return null;
  }
}

export function AuthProvider({ children }) {
  const [session, setSessionState] = useState(readStoredSession); // { token, username, userId, role } | null

  const setSession = useCallback((next) => {
    setSessionState(next);
    try {
      if (next) {
        sessionStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      } else {
        sessionStorage.removeItem(STORAGE_KEY);
      }
    } catch {
      // sessionStorage write can fail (quota, private-browsing) -- the
      // in-memory session still works for the rest of this tab's life,
      // it just won't survive a refresh. Not worth failing login/logout
      // over.
    }
  }, []);

  const login = useCallback(
    async (username, password) => {
      const { access_token, userId, role } = await loginRequest(username, password);
      setSession({ token: access_token, username, userId, role });
    },
    [setSession]
  );

  const logout = useCallback(() => {
    setSession(null);
  }, [setSession]);

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
