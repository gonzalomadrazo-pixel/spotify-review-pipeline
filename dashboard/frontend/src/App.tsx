import { Link, NavLink, Route, Routes } from "react-router-dom";
import NeuralField from "./NeuralField";
import { fmt, useApi } from "./components";
import Overview from "./pages/Overview";
import Issues from "./pages/Issues";
import IssueDetail from "./pages/IssueDetail";
import Reviews from "./pages/Reviews";
import ReviewDetail from "./pages/ReviewDetail";
import Recommendation from "./pages/Recommendation";
import Method from "./pages/Method";

const NAV = [
  ["/", "Overview"],
  ["/recommendation", "Recommendation"],
  ["/issues", "Issue ranking"],
  ["/reviews", "Reviews"],
  ["/method", "Method & evidence"],
] as const;

/** A tiny neural graph: four product-area nodes wired to one core. */
function Logo() {
  const nodes: [number, number, string][] = [[5, 6, "#1490b8"], [21, 4, "#7b5ce6"], [23, 21, "#d6408e"], [6, 22, "#b56d0b"]];
  return (
    <svg className="logo" viewBox="0 0 28 28" aria-hidden="true">
      {nodes.map(([x, y], i) => <line key={i} x1={14} y1={14} x2={x} y2={y} stroke="#9c9586" strokeWidth={1.2} />)}
      <line x1={5} y1={6} x2={21} y2={4} stroke="#c7bda9" strokeWidth={1} />
      <line x1={23} y1={21} x2={6} y2={22} stroke="#c7bda9" strokeWidth={1} />
      {nodes.map(([x, y, c], i) => <circle key={i} cx={x} cy={y} r={2.6} fill={c} />)}
      <circle cx={14} cy={14} r={4} fill="#14130f" />
    </svg>
  );
}

export default function App() {
  const health = useApi<{ ok: boolean; reviews: number; database: string }>("/api/health");
  return (
    <>
      <NeuralField />
      <div className="shell">
        <header className="topbar">
          <Link to="/" className="wordmark"><Logo />Review Insights <small>Spotify · Android</small></Link>
          <nav className="topnav" aria-label="Sections">
            {NAV.map(([to, label]) => (
              <NavLink key={to} to={to} end={to === "/"}>{label}</NavLink>
            ))}
          </nav>
        </header>
        <main>
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/recommendation" element={<Recommendation />} />
            <Route path="/issues" element={<Issues />} />
            <Route path="/issues/:id" element={<IssueDetail />} />
            <Route path="/reviews" element={<Reviews />} />
            <Route path="/reviews/:id" element={<ReviewDetail />} />
            <Route path="/method" element={<Method />} />
            <Route path="*" element={<p>Page not found.</p>} />
          </Routes>
        </main>
        <footer className="foot">
          {health.data?.ok ? <span className="live">Live data</span> : <span>Connecting…</span>}
          {health.data && <span>{fmt(health.data.reviews)} reviews · {health.data.database} database</span>}
          <span>Read-only: browsing never calls a model</span>
          <span>Google Play reviews of Spotify for Android, May 2022 – Nov 2023</span>
        </footer>
      </div>
    </>
  );
}
