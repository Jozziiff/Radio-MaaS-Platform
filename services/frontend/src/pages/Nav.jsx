// Shared header nav (M6, continued): extracted from CatalogPage so History
// and any future page reuse the exact same bar instead of copying it --
// same Gitea link, same username/sign-out, just an active-page indicator
// added for the nav items themselves.
//
// No router: App.jsx holds a plain "catalog" | "history" page string and
// passes it down with a setter. Two pages doesn't justify pulling in a
// router dependency yet -- revisit if a third page or deep-linking
// actually becomes necessary.

import { useAuth } from "../auth/AuthContext";

export default function Nav({ page, onNavigate, giteaLink }) {
  const { session, logout } = useAuth();

  return (
    <header className="flex items-center justify-between border-b border-signal-700 px-6 py-4">
      <div className="flex items-center gap-6">
        <span className="font-mono text-sm font-medium tracking-tight text-signal-100">
          radio-maas
        </span>
        <nav className="flex items-center gap-4">
          <NavLink active={page === "catalog"} onClick={() => onNavigate("catalog")}>
            Catalog
          </NavLink>
          <NavLink active={page === "history"} onClick={() => onNavigate("history")}>
            History
          </NavLink>
        </nav>
      </div>
      <div className="flex items-center gap-4">
        {giteaLink}
        <span className="text-sm text-signal-400">
          <span className="font-mono text-signal-200">{session.username}</span>
        </span>
        <button
          onClick={logout}
          className="text-sm text-signal-400 transition-colors hover:text-signal-100"
        >
          Sign out
        </button>
      </div>
    </header>
  );
}

function NavLink({ active, onClick, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        active
          ? "text-sm font-medium text-amber-500"
          : "text-sm text-signal-400 transition-colors hover:text-signal-100"
      }
    >
      {children}
    </button>
  );
}
