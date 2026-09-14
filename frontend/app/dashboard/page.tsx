"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError, tokenKey, safeLink, type User, type Bill, type Carbon } from "../../lib/api";

export default function Dashboard() {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [bill, setBill] = useState<Bill | null>(null);
  const [carbon, setCarbon] = useState<Carbon | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    try {
      const token = sessionStorage.getItem(tokenKey);
      if (!token) { router.replace("/auth"); return; }
      api<User>("/auth/me", {}, token).then(value => { if (active) setUser(value); }).catch(error => {
        if (!active) return;
        if (error instanceof ApiError && error.status === 401) { sessionStorage.removeItem(tokenKey); router.replace("/auth"); }
        else setError("Unable to check your session. Please reload to retry.");
      });
    } catch { setError("Enable browser session storage to continue."); }
    return () => { active = false; };
  }, [router]);
  function logout() { sessionStorage.removeItem(tokenKey); setUser(null); setBill(null); setCarbon(null); router.replace("/auth"); }
  function failure(error: unknown) {
    if (error instanceof ApiError && error.status === 401) logout();
    else setError(error instanceof Error ? error.message : "Unable to complete the request.");
  }
  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(""); setBill(null); setCarbon(null);
    if (!file || !/\.(csv|pdf)$/i.test(file.name) || !file.size || file.size > 10 * 1024 * 1024) {
      setError("Choose a nonempty CSV or PDF file up to 10 MB."); return;
    }
    setBusy("Uploading and parsing…");
    try {
      const token = sessionStorage.getItem(tokenKey);
      if (!token) { logout(); return; }
      const body = new FormData(); body.append("file", file);
      setBill(await api<Bill>("/bills/upload", { method: "POST", body }, token));
    } catch (error) { failure(error); } finally { setBusy(""); }
  }
  async function calculate() {
    if (!bill) return;
    setBusy("Calculating…"); setError(""); setCarbon(null);
    try {
      const token = sessionStorage.getItem(tokenKey);
      if (!token) { logout(); return; }
      const items = bill.items.map(({ service_name, service_category, region, usage_quantity, usage_unit }) =>
        ({ service_name, service_category, region, usage_quantity, usage_unit }));
      setCarbon(await api<Carbon>("/carbon/calculate", { method: "POST", body: JSON.stringify({ provider: bill.provider, items }) }, token));
    } catch (error) { failure(error); } finally { setBusy(""); }
  }
  if (!user) return <main className="workspace"><h1>Your dashboard</h1><p role={error ? "alert" : "status"}>{error || "Checking your session…"}</p><a href="/auth">Back to sign in</a></main>;
  return <main className="workspace">
    <div className="actions spread"><div><span className="section-kicker">YOUR WORKSPACE</span><h1>Cloud usage, made clearer.</h1><p>{user.email}</p></div><button onClick={logout} disabled={!!busy}>Log out</button></div>
    <section className="panel" aria-labelledby="upload-title">
      <h2 id="upload-title">Analyze a cloud bill</h2>
      <p>CSV or text-readable PDF · Up to 10 MB. Files and results are not saved; leaving this page clears the analysis.</p>
      <form onSubmit={upload} className="form-stack">
        <label>Billing file<input type="file" accept=".csv,.pdf" required disabled={!!busy} onChange={e => { setFile(e.target.files?.[0] || null); setBill(null); setCarbon(null); setError(""); }} /></label>
        <button className="button" disabled={!!busy || !file}>Upload and parse</button>
      </form>
      <p role="status">{busy}</p>
      {error && <p role="alert" className="error">{error}</p>}
    </section>
    {bill && <section className="panel" aria-labelledby="parsed-title">
      <h2 id="parsed-title">Parsed bill</h2>
      <p>{bill.filename} · {bill.file_type.toUpperCase()} · Provider: {bill.provider ?? "Unknown"} · {bill.row_count} items</p>
      {bill.warnings.map((warning, i) => <p className="notice" key={i}>{warning}</p>)}
      <div className="table-scroll" tabIndex={0} role="region" aria-label="Parsed usage"><table><caption>Normalized billing usage</caption><thead><tr>{["Service", "Category", "Region", "Quantity", "Unit", "Cost", "Currency", "Billing period"].map(h => <th scope="col" key={h}>{h}</th>)}</tr></thead><tbody>
        {bill.items.map((item, i) => <tr key={i}>{[item.service_name, item.service_category, item.region, item.usage_quantity, item.usage_unit, item.cost, item.currency, item.billing_period].map((value, j) => <td key={j}>{value ?? "—"}</td>)}</tr>)}
      </tbody></table></div>
      <button className="button" onClick={calculate} disabled={!!busy || !bill.items.length}>Estimate carbon footprint</button>
    </section>}
    {carbon && <section className="panel" aria-labelledby="carbon-title">
      <h2 id="carbon-title">Carbon estimate</h2>
      {carbon.total_kg_co2e === null ? <p className="notice">No estimate is available. No verified coefficient is currently available for these usage items, or required usage fields are missing.</p> : <p className="estimate">{carbon.total_kg_co2e} kg CO₂e{!carbon.complete && " · Partial estimate"}</p>}
      <p>Methodology: {carbon.methodology_version}</p>
      {carbon.warnings.map((warning, i) => <p className="notice" key={i}>{warning}</p>)}
      {carbon.calculated_items.length > 0 && <><h3>Calculated usage</h3><div className="table-scroll" tabIndex={0} role="region" aria-label="Calculated usage"><table><thead><tr><th scope="col">Service / provider / region</th><th scope="col">kg CO₂e</th><th scope="col">Source</th></tr></thead><tbody>{carbon.calculated_items.map((item, i) => <tr key={i}><td>{item.service_name ?? "Unknown"} / {item.provider ?? "Unknown"} / {item.region ?? "Unknown"}</td><td>{item.estimated_kg_co2e}</td><td>{safeLink(item.source.url) ? <a href={safeLink(item.source.url)} target="_blank" rel="noopener noreferrer">{item.source.name}</a> : item.source.name} · {item.source.methodology_version}</td></tr>)}</tbody></table></div></>}
      {carbon.unsupported_items.length > 0 && <><h3>Unsupported usage ({carbon.unsupported_items.length})</h3><p>These emissions are unknown and are excluded from the estimate.</p><div className="table-scroll" tabIndex={0} role="region" aria-label="Unsupported usage"><table><thead><tr><th scope="col">Service</th><th scope="col">Region</th><th scope="col">Reason</th></tr></thead><tbody>{carbon.unsupported_items.map((item, i) => <tr key={i}><td>{item.service_name ?? "Unknown"}</td><td>{item.region ?? "Unknown"}</td><td>{item.reason}</td></tr>)}</tbody></table></div></>}
    </section>}
  </main>;
}
