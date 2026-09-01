import { useState } from "react";
import { useAuth } from "../auth/AuthContext";
import Button from "../components/Button";
import Card from "../components/Card";
import AmbientGlow from "../components/AmbientGlow";

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
    <div className="relative min-h-screen flex items-center justify-center bg-signal-950 px-4">
      <AmbientGlow />

      <div className="relative z-10 w-full max-w-sm">
        <div className="mb-8 text-center">
          <img
            src="/Orange-logo.png"
            alt="Orange"
            className="mx-auto mb-4 h-14 w-14 object-contain drop-shadow-[0_0_18px_rgba(245,165,36,0.45)]"
          />
          <h1 className="font-mono text-lg font-medium tracking-tight text-signal-100">
            radio-maas
          </h1>
          <p className="mt-1.5 text-sm font-medium text-amber-500">Macro-as-a-Service</p>
          <p className="mt-0.5 text-sm text-signal-400">RADIO-OPTIM control panel</p>
        </div>

        <Card
          as="form"
          level="accent"
          onSubmit={handleSubmit}
          className="p-7 shadow-2xl shadow-black/40 [box-shadow:0_0_60px_-20px_var(--color-amber-500),0_25px_50px_-12px_rgba(0,0,0,0.4)]"
        >
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
