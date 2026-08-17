// Shell (M6, design pass): the app frame every authenticated page renders
// inside -- Sidebar on the left, scrollable content area (containing a
// TopBar + the page's own body) on the right. Login is the one screen
// that does NOT use this, since there's no session/nav to show yet.

import Sidebar from "./Sidebar";

export default function Shell({ page, onNavigate, giteaLink, children }) {
  return (
    <div className="flex min-h-screen bg-signal-950">
      <Sidebar page={page} onNavigate={onNavigate} giteaLink={giteaLink} />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}
