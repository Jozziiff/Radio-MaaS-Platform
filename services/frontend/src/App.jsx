import { useEffect, useState } from "react";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import LoginPage from "./pages/LoginPage";
import CatalogPage from "./pages/CatalogPage";
import HistoryPage from "./pages/HistoryPage";
import MapPage from "./pages/MapPage";
import AdminPage from "./pages/AdminPage";
import AboutPage from "./pages/AboutPage";

// No router: just a handful of pages behind a session gate, so a plain
// "catalog" | "history" | "map" | "admin" | "about" string is enough --
// see components/Sidebar.jsx for the shared shell.
function Routes() {
  const { session } = useAuth();
  const [page, setPage] = useState("catalog");

  // M7: guards the admin page itself, not just Sidebar's link visibility --
  // an employee session that lands on page === "admin" (e.g. stale state
  // from before a role change, not just a URL a nonexistent router could
  // block) is bounced back to the catalog before AdminPage ever mounts and
  // starts firing GET /users, which would just 403 anyway.
  useEffect(() => {
    if (page === "admin" && session?.role !== "admin") {
      setPage("catalog");
    }
  }, [page, session]);

  if (!session) return <LoginPage />;
  if (page === "history") return <HistoryPage page={page} onNavigate={setPage} />;
  if (page === "map") return <MapPage page={page} onNavigate={setPage} />;
  if (page === "about") return <AboutPage page={page} onNavigate={setPage} />;
  if (page === "admin" && session.role === "admin") {
    return <AdminPage page={page} onNavigate={setPage} />;
  }
  return <CatalogPage page={page} onNavigate={setPage} />;
}

export default function App() {
  return (
    <AuthProvider>
      <Routes />
    </AuthProvider>
  );
}
