import { useState } from "react";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import LoginPage from "./pages/LoginPage";
import CatalogPage from "./pages/CatalogPage";
import HistoryPage from "./pages/HistoryPage";

// No router: just two pages behind a session gate, so a plain "catalog" |
// "history" string is enough -- see components/Sidebar.jsx for the shared shell.
function Routes() {
  const { session } = useAuth();
  const [page, setPage] = useState("catalog");

  if (!session) return <LoginPage />;
  if (page === "history") return <HistoryPage page={page} onNavigate={setPage} />;
  return <CatalogPage page={page} onNavigate={setPage} />;
}

export default function App() {
  return (
    <AuthProvider>
      <Routes />
    </AuthProvider>
  );
}
