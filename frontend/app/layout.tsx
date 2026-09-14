import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "TopGreenCloud | Clearer choices for a greener cloud",
  description:
    "Explore cloud sustainability with sourced comparisons and a transparent approach to carbon estimates.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <div className="site-shell">
          <header className="site-header">
            <a className="brand" href="/" aria-label="TopGreenCloud home">
              <span className="brand-mark" aria-hidden="true">✳</span>
              TopGreenCloud
            </a>
            <nav aria-label="Main navigation">
              <a href="/#providers">Compare providers</a>
              <a className="nav-link" href="/dashboard">Dashboard <span aria-hidden="true">↗</span></a>
            </nav>
          </header>
          {children}
          <footer className="site-footer">
            <span>TopGreenCloud</span>
            <span>Better questions for a greener cloud.</span>
          </footer>
        </div>
      </body>
    </html>
  );
}
