// Shell (M6, design pass; night-glow pass): the app frame every
// authenticated page renders inside -- Sidebar on the left, scrollable
// content area (containing a TopBar + the page's own body) on the right.
// Login is the one screen that does NOT use this, since there's no
// session/nav to show yet (it mounts its own AmbientGlow instead).
//
// AmbientGlow sits at z-0; Sidebar and the content column are explicitly
// z-10 so the glow is guaranteed to render behind every real element
// regardless of DOM order, not just via being first in markup.

import Sidebar from "./Sidebar";
import AmbientGlow from "./AmbientGlow";

export default function Shell({ page, onNavigate, giteaLink, children }) {
  return (
    <div className="relative flex min-h-screen bg-signal-950">
      <AmbientGlow />
      <Sidebar page={page} onNavigate={onNavigate} giteaLink={giteaLink} className="relative z-10" />
      <div className="relative z-10 min-w-0 flex-1">{children}</div>
    </div>
  );
}
