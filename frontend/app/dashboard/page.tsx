"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { api, apiBlob, ApiError, tokenKey, safeLink, type User, type Bill, type Carbon, type StoredBill, type AnalysisHistory } from "../../lib/api";

export default function Dashboard() {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [bill, setBill] = useState<Bill | null>(null);
  const [carbon, setCarbon] = useState<Carbon | null>(null);
  const [storedBills, setStoredBills] = useState<StoredBill[]>([]);
  const [history, setHistory] = useState<AnalysisHistory[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    try {
      const token = sessionStorage.getItem(tokenKey);
      if (!token) { router.replace("/auth"); return; }
      api<User>("/auth/me", {}, token).then(async value => {
        if (!active) return;
        setUser(value);
        const [bills, analyses] = await Promise.all([
          api<StoredBill[]>("/bills", {}, token), api<AnalysisHistory[]>("/carbon/history", {}, token),
        ]);
        if (active) { setStoredBills(bills); setHistory(analyses); }
      }).catch(error => {
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
  async function refreshRecords(token: string) {
    const [bills, analyses] = await Promise.all([
      api<StoredBill[]>("/bills", {}, token), api<AnalysisHistory[]>("/carbon/history", {}, token),
    ]);
    setStoredBills(bills); setHistory(analyses);
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
      const uploaded = await api<Bill>("/bills/upload", { method: "POST", body }, token);
      setBill(uploaded);
      if (uploaded.bill_id !== null) await refreshRecords(token);
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
      const result = await api<Carbon>("/carbon/calculate", { method: "POST", body: JSON.stringify({
        provider: bill.provider, bill_id: bill.bill_id, items,
      }) }, token);
      setCarbon(result);
      if (result.analysis_id !== null) await refreshRecords(token);
    } catch (error) { failure(error); } finally { setBusy(""); }
  }
  async function deleteBill(id: number) {
    if (!window.confirm("Delete this stored bill and all of its analysis history?")) return;
    setBusy("Deleting…"); setError("");
    try {
      const token = sessionStorage.getItem(tokenKey);
      if (!token) { logout(); return; }
      await api<void>(`/bills/${id}`, { method: "DELETE" }, token);
      if (bill?.bill_id === id) { setBill(null); setCarbon(null); }
      await refreshRecords(token);
    } catch (error) { failure(error); } finally { setBusy(""); }
  }
  async function exportCsv(analysisId: number) {
    setBusy("Preparing CSV…"); setError("");
    try {
      const token = sessionStorage.getItem(tokenKey);
      if (!token) { logout(); return; }
      const { blob, filename } = await apiBlob(`/carbon/history/${analysisId}/export.csv`, token);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (error) { failure(error); } finally { setBusy(""); }
  }
  if (!user) return <main className="workspace"><h1>Your dashboard</h1><p role={error ? "alert" : "status"}>{error || "Checking your session…"}</p><a href="/auth">Back to sign in</a></main>;
  return <main className="workspace">
    <div className="actions spread"><div><span className="section-kicker">YOUR WORKSPACE</span><h1>Cloud usage, made clearer.</h1><p>{user.email}</p></div><button onClick={logout} disabled={!!busy}>Log out</button></div>
    <section className="panel" aria-labelledby="upload-title">
      <h2 id="upload-title">Analyze a cloud bill</h2>
      <p>CSV or text-readable PDF · Up to 10 MB. Cloud-backed uploads are saved; local stateless uploads remain available only for this session.</p>
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
      <p className="notice">{bill.bill_id === null ? "This upload is session-only." : "This bill is stored securely and can be managed below."}</p>
      <div className="table-scroll" tabIndex={0} role="region" aria-label="Parsed usage"><table><caption>Normalized billing usage</caption><thead><tr>{["Service", "Category", "Region", "Quantity", "Unit", "Cost", "Currency", "Billing period"].map(h => <th scope="col" key={h}>{h}</th>)}</tr></thead><tbody>
        {bill.items.map((item, i) => <tr key={i}>{[item.service_name, item.service_category, item.region, item.usage_quantity, item.usage_unit, item.cost, item.currency, item.billing_period].map((value, j) => <td key={j}>{value ?? "—"}</td>)}</tr>)}
      </tbody></table></div>
      <button className="button" onClick={calculate} disabled={!!busy || !bill.items.length}>Estimate carbon footprint</button>
    </section>}
    {carbon && <section className="panel" aria-labelledby="carbon-title">
      <h2 id="carbon-title">Carbon estimate</h2>
      {carbon.total_kg_co2e === null ? <p className="notice">No estimate is available. No verified coefficient is currently available for these usage items, or required usage fields are missing.</p> : <p className="estimate">{carbon.total_kg_co2e} kg CO₂e{!carbon.complete && " · Partial estimate"}</p>}
      <p>Methodology: {carbon.methodology_version}</p>
      <p className="notice">{carbon.analysis_id === null ? "This result is session-only." : "This analysis was saved to your history."}</p>
      {carbon.warnings.map((warning, i) => <p className="notice" key={i}>{warning}</p>)}
      {carbon.calculated_items.length > 0 && <><h3>Calculated usage</h3><div className="table-scroll" tabIndex={0} role="region" aria-label="Calculated usage"><table><thead><tr><th scope="col">Service / provider / region</th><th scope="col">kg CO₂e</th><th scope="col">Source</th></tr></thead><tbody>{carbon.calculated_items.map((item, i) => <tr key={i}><td>{item.service_name ?? "Unknown"} / {item.provider ?? "Unknown"} / {item.region ?? "Unknown"}</td><td>{item.estimated_kg_co2e}</td><td>{safeLink(item.source.url) ? <a href={safeLink(item.source.url)} target="_blank" rel="noopener noreferrer">{item.source.name}</a> : item.source.name} · {item.source.methodology_version}</td></tr>)}</tbody></table></div></>}
      {carbon.unsupported_items.length > 0 && <><h3>Unsupported usage ({carbon.unsupported_items.length})</h3><p>These emissions are unknown and are excluded from the estimate.</p><div className="table-scroll" tabIndex={0} role="region" aria-label="Unsupported usage"><table><thead><tr><th scope="col">Service</th><th scope="col">Region</th><th scope="col">Reason</th></tr></thead><tbody>{carbon.unsupported_items.map((item, i) => <tr key={i}><td>{item.service_name ?? "Unknown"}</td><td>{item.region ?? "Unknown"}</td><td>{item.reason}</td></tr>)}</tbody></table></div></>}
    </section>}
    <section className="panel" aria-labelledby="stored-bills-title">
      <h2 id="stored-bills-title">Stored bills</h2>
      {!storedBills.length ? <p>No persisted bills are available.</p> : <div className="table-scroll" tabIndex={0} role="region" aria-label="Stored bills"><table><thead><tr><th scope="col">File</th><th scope="col">Type</th><th scope="col">Uploaded</th><th scope="col">Status</th><th scope="col">Action</th></tr></thead><tbody>
        {storedBills.map(item => <tr key={item.id}><td>{item.original_filename}</td><td>{item.file_type.toUpperCase()}</td><td>{new Date(item.uploaded_at).toLocaleString()}</td><td>{item.status}</td><td><button onClick={() => deleteBill(item.id)} disabled={!!busy}>Delete</button></td></tr>)}
      </tbody></table></div>}
      <p>Deleting a stored bill also removes its associated analysis history.</p>
    </section>
    <section className="panel" aria-labelledby="history-title">
      <h2 id="history-title">Analysis history</h2>
      {!history.length ? <p>No saved analyses are available.</p> : <div className="table-scroll" tabIndex={0} role="region" aria-label="Analysis history"><table><thead><tr><th scope="col">Bill</th><th scope="col">Date</th><th scope="col">Provider</th><th scope="col">Total</th><th scope="col">Coverage</th><th scope="col">Methodology</th><th scope="col">Export</th></tr></thead><tbody>
        {history.map(item => <tr key={item.analysis_id}><td>{item.bill_filename}</td><td>{new Date(item.created_at).toLocaleString()}</td><td>{item.provider_slug ?? "—"}</td><td>{item.total_kg_co2e === null ? "No supported estimate" : `${item.total_kg_co2e} kg CO₂e`}</td><td>{item.complete ? "Complete" : "Partial"}</td><td>{item.methodology_version}</td><td><button onClick={() => exportCsv(item.analysis_id)} disabled={!!busy}>Export CSV</button></td></tr>)}
      </tbody></table></div>}
    </section>
  </main>;
}
