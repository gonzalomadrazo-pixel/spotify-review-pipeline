import { NavLink, Route, Routes } from "react-router-dom";
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

export default function App() {
  return (
    <div className="shell">
      <nav className="side">
        <div className="brand">Spotify Review Insights</div>
        <div className="brand-sub">Where should next quarter's product effort go?</div>
        {NAV.map(([to, label]) => (
          <NavLink key={to} to={to} end={to === "/"}>
            {label}
          </NavLink>
        ))}
      </nav>
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
    </div>
  );
}
