// Sidebar (M6, design pass): replaces Nav.jsx's horizontal header bar with
// a persistent left rail -- wordmark/logo at top, Catalog/History nav
// items with lucide icons, username + sign-out pinned to the bottom via
// mt-auto. This is the one shared shell every page now renders inside,
// instead of each page (or the old shared Nav) owning its own header.
//
// The Gitea instance link moved here from Nav's top-bar-right slot: it's
// navigation-shaped (an external link out of the app), not a page action,
// so it belongs alongside Catalog/History rather than floating in a
// per-page top bar.

import { LayoutGrid, History, LogOut, Radio } from "lucide-react";
import { useAuth } from "../auth/AuthContext";

export default function Sidebar({ page, onNavigate, giteaLink, className = "" }) {
  const { session, logout } = useAuth();

  return (
    <aside
      className={`flex h-screen w-56 shrink-0 flex-col border-r border-signal-700 bg-signal-900 px-4 py-5 ${className}`}
    >

      <div className="flex items-center gap-2 px-2">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-amber-500/10 text-amber-500">
          <Radio className="h-4 w-4" strokeWidth={2} />
        </span>
        <span className="font-mono text-sm font-medium tracking-tight text-signal-100">
          radio-maas
        </span>
      </div>

      <nav className="mt-8 flex flex-col gap-1">
        <SidebarLink
          icon={LayoutGrid}
          label="Catalog"
          active={page === "catalog"}
          onClick={() => onNavigate("catalog")}
        />
        <SidebarLink
          icon={History}
          label="History"
          active={page === "history"}
          onClick={() => onNavigate("history")}
        />
      </nav>

      {giteaLink && <div className="mt-4 px-2">{giteaLink}</div>}

      <div className="mt-auto flex flex-col gap-3 border-t border-signal-700 pt-4">
        <div className="flex items-center gap-2 px-2">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-signal-800 font-mono text-xs text-signal-200">
            {session.username.slice(0, 2).toUpperCase()}
          </span>
          <span className="truncate font-mono text-sm text-signal-200">{session.username}</span>
        </div>
        <button
          onClick={logout}
          className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-signal-400 transition-colors hover:bg-signal-800 hover:text-signal-100"
        >
          <LogOut className="h-4 w-4" strokeWidth={1.75} />
          Sign out
        </button>
      </div>
    </aside>
  );
}

function SidebarLink({ icon: Icon, label, active, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        active
          ? "relative flex items-center gap-2.5 rounded-lg bg-amber-500/10 px-3 py-2 text-sm font-medium text-amber-500 shadow-[0_0_16px_-4px_var(--color-amber-500)] before:absolute before:-left-1 before:top-1/2 before:h-4 before:w-0.5 before:-translate-y-1/2 before:rounded-full before:bg-amber-500"
          : "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-signal-400 transition-colors hover:bg-signal-800 hover:text-signal-100"
      }
    >
      <Icon className="h-4 w-4" strokeWidth={1.75} />
      {label}
    </button>
  );
}
