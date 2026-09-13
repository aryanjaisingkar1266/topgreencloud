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
