import { useEffect, useRef } from "react";

/** Decorative background: a slowly drifting neural network. Neutral nodes and hairline synapses; signal pulses
 * travel along the links in the four product-area colors and the network leans toward the pointer.
 * aria-hidden, and static when the viewer prefers reduced motion. */

type Node = { x: number; y: number; vx: number; vy: number; bx: number; by: number; r: number; phase: number };
type Pulse = { a: number; b: number; t: number; speed: number; rgb: string };

const PULSE_RGB = ["20,144,184", "123,92,230", "214,64,142", "181,109,11"]; // the area palette (theme.ts)
const LINK = 140;

export default function NeuralField() {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current!;
    const ctx = canvas.getContext("2d")!;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let w = 0, h = 0, raf = 0, last = performance.now(), nextPulse = 0;
    let nodes: Node[] = [];
    const pulses: Pulse[] = [];
    const pointer = { x: -9999, y: -9999 };
    const rand = (a: number, b: number) => a + Math.random() * (b - a);

    function seed() {
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      w = window.innerWidth;
      h = window.innerHeight;
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      pulses.length = 0; // pulses index into the old node list
      const n = Math.round(Math.min(90, Math.max(30, (w * h) / 17000)));
      nodes = Array.from({ length: n }, () => {
        const bx = rand(-0.1, 0.1), by = rand(-0.07, 0.07); // px per 16 ms
        return { x: rand(0, w), y: rand(0, h), vx: bx, vy: by, bx, by, r: Math.random() < 0.15 ? rand(1.6, 2.3) : rand(0.8, 1.4), phase: rand(0, 6.28) };
      });
    }

    function frame(now: number) {
      const dt = Math.min(50, now - last);
      last = now;
      ctx.clearRect(0, 0, w, h);
      if (!reduce) {
        for (const s of nodes) {
          const dx = pointer.x - s.x, dy = pointer.y - s.y, d2 = dx * dx + dy * dy;
          if (d2 < 170 * 170 && d2 > 400) { const d = Math.sqrt(d2); s.vx += (dx / d) * 0.01; s.vy += (dy / d) * 0.01; }
          s.vx += (s.bx - s.vx) * 0.02;
          s.vy += (s.by - s.vy) * 0.02;
          s.x += (s.vx * dt) / 16; s.y += (s.vy * dt) / 16;
          if (s.x < -20) s.x = w + 20; else if (s.x > w + 20) s.x = -20;
          if (s.y < -20) s.y = h + 20; else if (s.y > h + 20) s.y = -20;
        }
      }
      const links: [number, number][] = [];
      ctx.lineWidth = 0.7;
      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i];
        for (let j = i + 1; j < nodes.length; j++) {
          const b = nodes[j];
          const dx = a.x - b.x, dy = a.y - b.y;
          if (Math.abs(dx) > LINK || Math.abs(dy) > LINK) continue;
          const d = Math.hypot(dx, dy);
          if (d < LINK) {
            links.push([i, j]);
            ctx.strokeStyle = `rgba(20,19,15,${(1 - d / LINK) * 0.07})`;
            ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
          }
        }
        const pd = Math.hypot(a.x - pointer.x, a.y - pointer.y);
        if (pd < 180) {
          ctx.strokeStyle = `rgba(51,109,93,${(1 - pd / 180) * 0.3})`;
          ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(pointer.x, pointer.y); ctx.stroke();
        }
      }
      for (const s of nodes) {
        const tw = reduce ? 0.8 : 0.7 + 0.3 * Math.sin(now * 0.0012 + s.phase);
        ctx.fillStyle = `rgba(20,19,15,${0.22 * tw})`;
        ctx.beginPath(); ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2); ctx.fill();
      }
      if (!reduce) {
        if (now > nextPulse && links.length && pulses.length < 10) {
          const [a, b] = links[Math.floor(Math.random() * links.length)];
          pulses.push({ a, b, t: 0, speed: rand(0.0006, 0.0012), rgb: PULSE_RGB[Math.floor(Math.random() * PULSE_RGB.length)] });
          nextPulse = now + rand(160, 460);
        }
        for (let k = pulses.length - 1; k >= 0; k--) {
          const p = pulses[k];
          p.t += p.speed * dt;
          const A = nodes[p.a], B = nodes[p.b];
          if (p.t >= 1 || !A || !B) { pulses.splice(k, 1); continue; }
          const x = A.x + (B.x - A.x) * p.t, y = A.y + (B.y - A.y) * p.t;
          // a short bright segment trailing the pulse, like a firing synapse
          const tx = A.x + (B.x - A.x) * Math.max(0, p.t - 0.18), ty = A.y + (B.y - A.y) * Math.max(0, p.t - 0.18);
          const grad = ctx.createLinearGradient(tx, ty, x, y);
          grad.addColorStop(0, `rgba(${p.rgb},0)`); grad.addColorStop(1, `rgba(${p.rgb},0.75)`);
          ctx.strokeStyle = grad; ctx.lineWidth = 1.6;
          ctx.beginPath(); ctx.moveTo(tx, ty); ctx.lineTo(x, y); ctx.stroke();
          ctx.fillStyle = `rgba(${p.rgb},1)`;
          ctx.beginPath(); ctx.arc(x, y, 1.8, 0, Math.PI * 2); ctx.fill();
        }
        raf = requestAnimationFrame(frame);
      }
    }

    const onMove = (e: PointerEvent) => { pointer.x = e.clientX; pointer.y = e.clientY; };
    const onLeave = () => { pointer.x = -9999; pointer.y = -9999; };
    let resizeTimer = 0;
    const onResize = () => {
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(() => { seed(); if (reduce) frame(performance.now()); }, 150);
    };
    seed();
    if (reduce) frame(performance.now());
    else raf = requestAnimationFrame(frame);
    window.addEventListener("pointermove", onMove, { passive: true });
    document.addEventListener("pointerleave", onLeave);
    window.addEventListener("resize", onResize);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerleave", onLeave);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  return <canvas ref={ref} className="neural" aria-hidden="true" />;
}
