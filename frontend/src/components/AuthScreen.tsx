import { FormEvent, useState } from "react";
import { api, ApiError } from "../api";

interface Props {
  onAuthed: (user: { id: number; email: string; display_name: string }) => void;
}

export function AuthScreen({ onAuthed }: Props) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!email || !password) return;
    setBusy(true);
    setError(null);
    try {
      const res = mode === "login" ? await api.login(email, password) : await api.register(email, password);
      // cookie is set by the response — fetch the canonical user object
      const u = await api.me();
      onAuthed(u as { id: number; email: string; display_name: string });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-wrap">
      <form className="card auth-card" onSubmit={submit}>
        <div className="brand big">
          <span className="brand-dot" /> CryptoDash
        </div>
        <p className="muted">Multi-timeframe signal desk — classic TA · ICT · sentiment · macro.</p>

        <label>email</label>
        <input type="text" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" />
        <label>password {mode === "register" ? "(10+ chars)" : ""}</label>
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={mode === "login" ? "current-password" : "new-password"} />

        {error && <div className="alert">{error}</div>}

        <button className="primary full" disabled={busy}>
          {busy ? "working…" : mode === "login" ? "sign in" : "create account"}
        </button>
        <div className="switch">
          {mode === "login" ? (
            <>new here? <a onClick={() => setMode("register")}>create an account</a></>
          ) : (
            <>have an account? <a onClick={() => setMode("login")}>sign in</a></>
          )}
        </div>
      </form>
    </div>
  );
}
