import { AuthProvider, useAuth } from "./auth/AuthContext";
import LoginPage from "./pages/LoginPage";
import CatalogPage from "./pages/CatalogPage";

function Routes() {
  const { session } = useAuth();
  return session ? <CatalogPage /> : <LoginPage />;
}

export default function App() {
  return (
    <AuthProvider>
      <Routes />
    </AuthProvider>
  );
}
