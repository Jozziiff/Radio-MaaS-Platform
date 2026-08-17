import { useState } from "react";
import { useAuth } from "../auth/AuthContext";
import Button from "../components/Button";
import Card from "../components/Card";

export default function LoginPage() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(username, password);
    } catch (err) {
      setError(err.message || "login failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-signal-950 px-4">
      {/* faint radiating-signal backdrop, purely decorative */}
      <div
        aria-hidden="true"
        className="pointer-events-none fixed inset-0 opacity-[0.07]"
        style={{
          backgroundImage:
            "radial-gradient(circle at 50% 35%, var(--color-amber-500) 0%, transparent 45%)",
        }}
      />

      <div className="relative w-full max-w-sm">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-4 flex h-11 w-11 items-center justify-center rounded-full border border-signal-600 bg-signal-900">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              className="h-5 w-5 text-amber-500"
              aria-hidden="true"
            >
              <path
                d="M12 2a10 10 0 0 1 0 20M12 6a6 6 0 0 1 0 12M12 10a2 2 0 0 1 0 4"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
              />
            </svg>
          </div>
          <h1 className="font-mono text-lg font-medium tracking-tight text-signal-100">
            radio-maas
          </h1>
          <p className="mt-1 text-sm text-signal-400">
            Macro-as-a-Service &middot; RADIO-OPTIM control panel
          </p>
        </div>

        <Card as="form" onSubmit={handleSubmit} className="p-7 shadow-2xl shadow-black/40">
          <div className="space-y-4">
            <Field
              label="Username"
              id="username"
              type="text"
              value={username}
              onChange={setUsername}
              autoFocus
              autoComplete="username"
            />
            <Field
              label="Password"
              id="password"
              type="password"
              value={password}
              onChange={setPassword}
              autoComplete="current-password"
            />
          </div>

          {error && (
            <div
              role="alert"
              className="mt-4 flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger"
            >
              <svg
                viewBox="0 0 20 20"
                fill="currentColor"
                className="mt-0.5 h-4 w-4 shrink-0"
                aria-hidden="true"
              >
                <path
                  fillRule="evenodd"
                  d="M10 18a8 8 0 100-16 8 8 0 000 16zm.75-11a.75.75 0 00-1.5 0v4a.75.75 0 001.5 0V7zm-.75 6.5a.875.875 0 100 1.75.875.875 0 000-1.75z"
                  clipRule="evenodd"
                />
              </svg>
              <span>{error}</span>
            </div>
          )}

          <Button type="submit" disabled={submitting} className="mt-6 w-full">
            {submitting ? "Signing in…" : "Sign in"}
          </Button>
        </Card>

        <p className="mt-6 text-center text-xs text-signal-400">
          Orange Tunisie &middot; internal tool &middot; dev environment
        </p>
      </div>
    </div>
  );
}

function Field({ label, id, type, value, onChange, autoFocus, autoComplete }) {
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-xs font-medium text-signal-400">
        {label}
      </label>
      <input
        id={id}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoFocus={autoFocus}
        autoComplete={autoComplete}
        required
        className="w-full rounded-lg border border-signal-600 bg-signal-800 px-3 py-2 text-sm text-signal-100 placeholder-signal-400 outline-none transition-colors focus:border-amber-500 focus:ring-1 focus:ring-amber-500"
      />
    </div>
  );
}
