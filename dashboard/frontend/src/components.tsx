import { ReactNode, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, Fact, get } from "./api";

export function useApi<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    if (!path) return;
    let live = true;
    setLoading(true);
    setError(null);
    get<T>(path)
      .then((d) => live && setData(d))
      .catch((e: unknown) => live && setError(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e)))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [path]);
  return { data, error, loading };
}

export function Status({ loading, error }: { loading: boolean; error: string | null }) {
  if (error) return <div className="notice error">Could not load data from the backend ({error}).</div>;
  if (loading) return <div className="notice loading">Loading…</div>;
  return null;
}

export function Card({ title, children, actions, className }: { title?: ReactNode; children: ReactNode; actions?: ReactNode; className?: string }) {
  return (
    <section className={`card ${className ?? ""}`}>
      {(title || actions) && (
        <header className="card-head">
          {title && <h2>{title}</h2>}
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

/** Report-style numbered section: kicker with number, a statement headline, optional dek, content, source line. */
export function Section({ id, n, kicker, headline, dek, source, children }: {
  id?: string; n: number; kicker: string; headline: ReactNode; dek?: ReactNode; source?: ReactNode; children: ReactNode;
}) {
  return (
    <section className="section" id={id}>
      <header className="section-head">
        <div className="sec-kicker"><span className="n">{String(n).padStart(2, "0")}</span>{kicker}</div>
        <h2 className="headline">{headline}</h2>
        {dek && <p className="dek">{dek}</p>}
      </header>
      {children}
      {source && <p className="source"><b>Source:</b> {source}</p>}
    </section>
  );
}

/** Sticky table of contents that marks the section currently in view. */
export function Contents({ items }: { items: [string, string][] }) {
  const [on, setOn] = useState(items[0]?.[0]);
  useEffect(() => {
    const els = items.map(([id]) => document.getElementById(id)).filter(Boolean) as HTMLElement[];
    const io = new IntersectionObserver((entries) => {
      const vis = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
      if (vis[0]) setOn(vis[0].target.id);
    }, { rootMargin: "-20% 0px -65% 0px" });
    els.forEach((e) => io.observe(e));
    return () => io.disconnect();
  }, [items]);
  return (
    <nav className="toc" aria-label="On this page">
      <div className="toc-title">On this page</div>
      {items.map(([id, label]) => <a key={id} href={`#${id}`} className={on === id ? "on" : ""}>{label}</a>)}
    </nav>
  );
}

export function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

export function Sev({ value }: { value: number | null }) {
  if (value == null) return <span className="muted">–</span>;
  return <span className={`sev sev-${value}`} title={SEVERITY[value]}>{value}</span>;
}

export const SEVERITY: Record<number, string> = {
  1: "No reported problem (praise, unclear, pure request)",
  2: "Dislike, generic criticism or minor annoyance",
  3: "Degraded or restricted function; some use remains",
  4: "A clearly blocked core task",
  5: "Explicit serious financial, privacy or data harm",
};

export function Tag({ children, kind }: { children: ReactNode; kind?: string }) {
  return <span className={`tag ${kind ?? ""}`}>{children}</span>;
}

export function IssueLink({ id }: { id: string }) {
  return <Link className="mono" to={`/issues/${encodeURIComponent(id)}`}>{id}</Link>;
}

export function ReviewLink({ id }: { id: string }) {
  return <Link className="mono" to={`/reviews/${encodeURIComponent(id)}`}>{id.slice(0, 8)}…</Link>;
}

export const fmt = (n: number | null | undefined) => (n == null ? "–" : n.toLocaleString("en-US"));

export function Pager({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="pager">
      <button disabled={page <= 1} onClick={() => onPage(page - 1)}>‹ Prev</button>
      <span>Page {page} of {fmt(pages)} · {fmt(total)} rows</span>
      <button disabled={page >= pages} onClick={() => onPage(page + 1)}>Next ›</button>
    </div>
  );
}

export function Highlight({ text, quote }: { text: string; quote: string | null }) {
  if (!quote) return <>{text}</>;
  const i = text.indexOf(quote);
  if (i < 0 || quote === text.trim()) return <>{text}</>;
  return (
    <>
      {text.slice(0, i)}
      <mark>{quote}</mark>
      {text.slice(i + quote.length)}
    </>
  );
}

/** Minimal, injection-safe Markdown for model-written memos: headings, lists, paragraphs, **bold**, `code`
 * and [F12] fact citations. Text is rendered as React text nodes, never as raw HTML. */
export function Markdown({ source, facts, issueIds }: { source: string; facts: Record<string, Fact>; issueIds: Set<string> }) {
  const lines = source.replace(/<!--[\s\S]*?-->/g, "").split("\n");
  const blocks: ReactNode[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  let para: string[] = [];
  const flushPara = () => {
    if (para.length) blocks.push(<p key={blocks.length}>{inline(para.join(" "), facts, issueIds)}</p>);
    para = [];
  };
  const flushList = () => {
    if (list) {
      const items = list.items.map((t, i) => <li key={i}>{inline(t, facts, issueIds)}</li>);
      blocks.push(list.ordered ? <ol key={blocks.length}>{items}</ol> : <ul key={blocks.length}>{items}</ul>);
    }
    list = null;
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    const ul = /^\s*[-*]\s+(.*)$/.exec(line);
    const ol = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (h) {
      flushPara();
      flushList();
      const level = Math.min(4, h[1].length + 1);
      const Tagname = `h${level}` as "h2" | "h3" | "h4";
      blocks.push(<Tagname key={blocks.length}>{inline(h[2], facts, issueIds)}</Tagname>);
    } else if (ul || ol) {
      flushPara();
      const ordered = !!ol;
      if (!list || list.ordered !== ordered) {
        flushList();
        list = { ordered, items: [] };
      }
      list.items.push((ul ?? ol)![1]);
    } else if (!line.trim()) {
      flushPara();
      flushList();
    } else {
      flushList();
      para.push(line.trim());
    }
  }
  flushPara();
  flushList();
  return <div className="markdown">{blocks}</div>;
}

function inline(text: string, facts: Record<string, Fact>, issueIds: Set<string>): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\[F\d+\])/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) out.push(<strong key={out.length}>{tok.slice(2, -2)}</strong>);
    else if (tok.startsWith("`")) {
      const code = tok.slice(1, -1);
      if (issueIds.has(code)) out.push(<IssueLink key={out.length} id={code} />);
      else if (/^[0-9a-f]{8}-[0-9a-f-]{27,}$/i.test(code)) out.push(<ReviewLink key={out.length} id={code} />);
      else out.push(<code key={out.length}>{code}</code>);
    } else {
      const id = tok.slice(1, -1);
      const f = facts[id];
      out.push(
        <a key={out.length} className="cite" href={`#fact-${id}`} title={f ? `${f.subject} · ${f.metric} = ${f.value} (${f.formula})` : "unknown fact"}>
          {id}
        </a>,
      );
    }
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
