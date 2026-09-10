import { FormEvent, useEffect, useState } from "react";
import { api } from "../api";

interface Props {
  onClose: () => void;
  onSaved: () => void;
}

export function SettingsModal({ onClose, onSaved }: Props) {
  const [status, setStatus] = useState<{ has_fred_key: boolean; server_wide_key_configured: boolean } | null>(null);
  const [key, setKey] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.secretsStatus().then(setStatus).catch(() => undefined);
  }, []);

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.setFredKey(key.trim() ? key.trim() : null);
      setStatus({ has_fred_key: r.has_fred_key, server_wide_key_configured: false });
      if (r.has_fred_key) setMsg("FRED key verified against the live API and stored encrypted.");
      else setMsg("FRED key removed — M2 will fall back to available sources only.");
      onSaved();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "failed to save");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-back" onClick={onClose}>
      <form className="card modal" onSubmit={save} onClick={(e) => e.stopPropagation()}>
        <h3>Settings</h3>
        {status?.server_wide_key_configured && <p className="small ok">Server-wide FRED key configured — M2 money supply is active.</p>}

        <label>FRED API key (optional — unlocks real M2 money supply data)</label>
        <input value={key} onChange={(e) => setKey(e.target.value)} placeholder="32-char key from fred.stlouisfed.org/api" />
        {status?.has_fred_key && <p className="small ok">Your personal FRED key is stored (encrypted at rest).</p>}

        {msg && <p className={`small ${msg.startsWith("FRED key verified") || msg.startsWith("Server-wide") ? "ok" : "warn"}`}>{msg}</p>}
        <button type="submit" className="primary" disabled={busy}>{busy ? "saving…" : "save FRED key"}</button>
        {status?.has_fred_key && (
          <>
            <hr />
            <button type="button" className="ghost danger" onClick={() => setKey("")}>remove my key…</button>
          </>
        )}

        <div className="modal-foot">
          <span className="muted small">Stored encrypted with AES (Fernet). Never returned to the UI.</span>
          <button className="ghost" onClick={onClose}>close</button>
        </div>
      </form>
    </div>
  );
}
