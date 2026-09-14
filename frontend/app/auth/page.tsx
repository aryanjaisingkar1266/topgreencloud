"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { api, tokenKey } from "../../lib/api";

export default function AuthPage() {
  const router = useRouter();
  const [register, setRegister] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(""); setMessage("");
    try {
      const body = JSON.stringify({ email: email.trim(), password });
      if (register) {
        await api("/auth/register", { method: "POST", body });
        setRegister(false); setPassword(""); setMessage("Account created. Sign in to continue.");
      } else {
        const result = await api<{ access_token: string }>("/auth/login", { method: "POST", body });
        try { sessionStorage.setItem(tokenKey, result.access_token); }
        catch { throw new Error("Enable browser session storage to sign in."); }
        setPassword(""); router.replace("/dashboard");
      }
    } catch (error) { setError(error instanceof Error ? error.message : "Unable to sign in."); }
    finally { setBusy(false); }
  }
  return <main className="workspace auth-panel">
    <span className="section-kicker">YOUR CLOUD, IN CONTEXT</span>
    <h1>{register ? "Create an account" : "Welcome back"}</h1>
    <p>Upload a bill and explore transparent usage estimates.</p>
    <div className="actions" aria-label="Account action">
      <button disabled={busy} aria-pressed={!register} onClick={() => { setRegister(false); setError(""); setMessage(""); }}>Sign in</button>
      <button disabled={busy} aria-pressed={register} onClick={() => { setRegister(true); setError(""); setMessage(""); }}>Register</button>
    </div>
    <form onSubmit={submit} className="panel form-stack">
      <label>Email<input type="email" autoComplete="email" required value={email} onChange={e => setEmail(e.target.value)} /></label>
      <label>Password<input type="password" autoComplete={register ? "new-password" : "current-password"} required minLength={register ? 12 : 1} maxLength={128} value={password} onChange={e => setPassword(e.target.value)} /></label>
      {register && <small>Use at least 12 characters.</small>}
      <button className="button" disabled={busy}>{busy ? (register ? "Creating account…" : "Signing in…") : (register ? "Create account" : "Sign in")}</button>
      {error && <p role="alert" className="error">{error}</p>}
      <p role="status">{message}</p>
    </form>
  </main>;
}
