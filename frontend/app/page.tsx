"use client";

import { useEffect, useState, type FormEvent } from "react";
import { api, safeLink, type Provider } from "../lib/api";

const areas = [
  {
    number: "01",
    title: "Compare with context",
    description: "Review provider commitments, efficiency metrics, and the sources behind each claim.",
  },
  {
    number: "02",
    title: "Understand your usage",
    description: "Turn cloud billing data into a clearer picture of your estimated footprint.",
  },
  {
    number: "03",
    title: "See what matters",
    description: "Follow trends and find where your biggest opportunities for change may be.",
  },
];

export default function Home() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [comparison, setComparison] = useState<Provider[]>([]);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("name");
  const [loading, setLoading] = useState(true);
  const [comparing, setComparing] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api<Provider[]>("/providers").then(data => { if (active) setProviders(data); })
      .catch(() => { if (active) setError("Unable to load providers. Please try again."); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);
  async function filter(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setLoading(true); setError(""); setComparison([]); setSelected([]);
    try {
      const query = new URLSearchParams({ sort });
      if (search.trim()) query.set("search", search.trim());
      setProviders(await api<Provider[]>(`/providers?${query}`));
    } catch { setProviders([]); setError("Unable to load providers. Please try again."); }
    finally { setLoading(false); }
  }
  async function compare() {
    setComparing(true); setError(""); setComparison([]);
    try { setComparison(await api<Provider[]>(`/providers/compare?${new URLSearchParams({ providers: selected.join(",") })}`)); }
    catch { setError("Unable to compare the selected providers. Reload the list and try again."); }
    finally { setComparing(false); }
  }
  return (
    <main>
      <section className="hero" aria-labelledby="hero-title">
        <div className="hero-copy">
          <span className="eyebrow"><span className="status-dot" /> SUSTAINABILITY, WITH SUBSTANCE</span>
          <h1 id="hero-title">A clearer view of your <em>cloud impact.</em></h1>
          <p>
            Cloud decisions shape more than performance and cost. TopGreenCloud is
            being built to make environmental information easier to compare,
            understand, and act on.
          </p>
          <a className="button" href="#what-is-coming">Explore the vision <span aria-hidden="true">↗</span></a>
          <span className="hero-note">Independent thinking. Transparent methodology.</span>
        </div>
        <div className="hero-art" aria-hidden="true">
          <div className="orbit orbit-outer" />
          <div className="orbit orbit-inner" />
          <div className="planet"><span className="planet-glow" /></div>
          <div className="art-label art-label-top">A BETTER PERSPECTIVE <span>↗</span></div>
          <div className="art-label art-label-bottom">CLOUD × CLIMATE <span>●</span></div>
        </div>
      </section>

      <section className="intro" id="approach" aria-labelledby="approach-title">
        <span className="section-kicker">THE APPROACH / 001</span>
        <h2 id="approach-title">Good decisions start with <span>better visibility.</span></h2>
        <p>Environmental claims deserve context. Usage estimates deserve a clear method. We&apos;re building a place for both.</p>
      </section>


      <section className="workspace" id="providers" aria-labelledby="providers-title">
        <span className="section-kicker">SOURCED INFORMATION</span>
        <h2 id="providers-title">Compare cloud providers</h2>
        <p>Select 2–4 providers. This is provider-published information, not a ranking. Reporting periods, company scope, and measurement methods differ; read each source and its qualifications before comparing.</p>
        <form className="actions" onSubmit={filter}>
          <label>Search providers<input value={search} maxLength={120} onChange={e => setSearch(e.target.value)} placeholder="Name or slug" /></label>
          <label>Sort<select value={sort} onChange={e => setSort(e.target.value)}><option value="name">Name: A–Z</option><option value="-name">Name: Z–A</option></select></label>
          <button disabled={loading || comparing}>Apply / reload</button>
        </form>
        {error && <p role="alert" className="error">{error}</p>}
        {loading ? <p role="status">Loading providers…</p> : providers.length === 0 && !error ? <p className="notice">No providers are available for this search. Verified records will appear here when added.</p> : <fieldset disabled={comparing}><legend>Choose providers ({selected.length}/4)</legend><div className="provider-grid">{providers.map(provider => <label className="panel" key={provider.id}>
          <span><input type="checkbox" checked={selected.includes(provider.slug)} disabled={selected.length === 4 && !selected.includes(provider.slug)} onChange={e => { setComparison([]); setSelected(e.target.checked ? [...selected, provider.slug] : selected.filter(slug => slug !== provider.slug)); }} /> {provider.name}</span>
          <span className="muted">{provider.description ?? "No description available."}</span>
        </label>)}</div></fieldset>}
        <button className="button" disabled={loading || comparing || selected.length < 2} onClick={compare}>{comparing ? "Comparing…" : "Compare selected providers"}</button>
        {comparison.length > 0 && <div className="table-scroll" tabIndex={0} role="region" aria-label="Provider comparison"><table><caption>Sustainability information in your selected order</caption><thead><tr>{comparison.map(provider => <th scope="col" key={provider.id}>{provider.name}</th>)}</tr></thead><tbody><tr>{comparison.map(provider => <td key={provider.id}>
          {safeLink(provider.website_url) && <p><a href={safeLink(provider.website_url)} target="_blank" rel="noopener noreferrer">Provider website ↗</a></p>}
          {!provider.metrics?.length && <p>No sustainability metrics published here yet.</p>}
          {provider.metrics?.map((metric, i) => <div className="metric" key={i}><strong>{metric.metric_name}</strong><p>{metric.metric_value}{metric.unit && ` ${metric.unit}`}</p>{metric.notes && <p>{metric.notes}</p>}{safeLink(metric.source_url) && <a href={safeLink(metric.source_url)} target="_blank" rel="noopener noreferrer">Read provider source ↗</a>}{metric.source_date && <p>Source date: {metric.source_date}</p>}</div>)}
        </td>)}</tr></tbody></table></div>}
      </section>

      <section className="features" id="what-is-coming" aria-labelledby="features-title">
        <div className="features-heading">
          <span className="section-kicker">ON THE ROADMAP / 002</span>
          <h2 id="features-title">The tools we&apos;re building</h2>
        </div>
        <div className="feature-grid">
          {areas.map((area) => (
            <article className="feature" key={area.number}>
              <span className="feature-number">{area.number} <span aria-hidden="true">↗</span></span>
              <h3>{area.title}</h3>
              <p>{area.description}</p>
            </article>
          ))}
        </div>
      </section>
    </main>
  );
}
