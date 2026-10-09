import { useEffect, useRef } from "react";

/** Hero illustration: a slowly rotating 3D neural network on a sphere. Nodes are wired to their nearest
 * neighbors; signals fire along the synapses in the four product-area colors; perspective and depth fading
 * give it volume, and the pointer tilts it. Decorative (aria-hidden); a still frame under reduced motion. */

const AREA_RGB = ["20,144,184", "123,92,230", "214,64,142", "181,109,11"]; // theme.ts light-mode steps
const INK = "20,19,15";

type P3 = { x: number; y: number; z: number; tint: number | null; r: number };
type Pulse = { a: number; b: number; t: number; speed: number; rgb: string };

export default function NeuralSphere({ nodes: count = 240 }: { nodes?: number }) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current!;
    const ctx = canvas.getContext("2d")!;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    // Fibonacci sphere with a little radial jitter for volume
    const pts: P3[] = [];
    const golden = Math.PI * (3 - Math.sqrt(5));
    for (let i = 0; i < count; i++) {
      const y = 1 - (i / (count - 1)) * 2;
      const rad = Math.sqrt(1 - y * y);
      const th = golden * i;
      const k = 0.86 + Math.random() * 0.14;
      pts.push({ x: Math.cos(th) * rad * k, y: y * k, z: Math.sin(th) * rad * k,
                 tint: Math.random() < 0.12 ? Math.floor(Math.random() * 4) : null, r: Math.random() < 0.1 ? 2.2 : 1.3 });
    }
    // synapses: each node to its 3 nearest neighbors
    const edges: [number, number][] = [];
    const seen = new Set<string>();
    pts.forEach((p, i) => {
      const near = pts.map((q, j) => ({ j, d: (p.x - q.x) ** 2 + (p.y - q.y) ** 2 + (p.z - q.z) ** 2 }))
        .filter((o) => o.j !== i).sort((a, b) => a.d - b.d).slice(0, 3);
      for (const { j } of near) {
        const key = i < j ? `${i}-${j}` : `${j}-${i}`;
        if (!seen.has(key)) { seen.add(key); edges.push([i, j]); }
      }
    });

    let size = 0, raf = 0, last = performance.now(), nextPulse = 0, rotY = 0.6, tiltX = -0.32, tx = -0.32, ty = 0, yo = 0;
    const pulses: Pulse[] = [];
    const proj = new Array(pts.length);

    function resize() {
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      size = canvas.parentElement!.clientWidth;
      canvas.width = size * dpr; canvas.height = size * dpr;
      canvas.style.width = `${size}px`; canvas.style.height = `${size}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function frame(now: number) {
      const dt = Math.min(50, now - last);
      last = now;
      if (!reduce) rotY += dt * 0.00011;
      tiltX += (tx - tiltX) * 0.05;
      yo += (ty - yo) * 0.05;
      const yaw = rotY + yo;
      const R = size * 0.36, cx = size / 2, cy = size / 2, f = 3.2;
      const cy_ = Math.cos(yaw), sy = Math.sin(yaw), cx_ = Math.cos(tiltX), sx = Math.sin(tiltX);
      for (let i = 0; i < pts.length; i++) {
        const p = pts[i];
        const x1 = p.x * cy_ + p.z * sy, z1 = -p.x * sy + p.z * cy_;
        const y2 = p.y * cx_ - z1 * sx, z2 = p.y * sx + z1 * cx_;
        const s = f / (f + z2);
        proj[i] = { x: cx + x1 * R * s, y: cy + y2 * R * s, z: z2, s, depth: (1 - z2) / 2 }; // depth 1 = front
      }
      ctx.clearRect(0, 0, size, size);

      // soft shadow pool under the sphere for a sense of space
      ctx.save(); // soft contact shadow under the sphere, for a sense of space
      ctx.translate(cx, cy + R * 1.2); ctx.scale(1, 0.14);
      const pool = ctx.createRadialGradient(0, 0, 0, 0, 0, R * 0.9);
      pool.addColorStop(0, "rgba(60,48,24,0.13)"); pool.addColorStop(0.6, "rgba(60,48,24,0.04)"); pool.addColorStop(1, "rgba(60,48,24,0)");
      ctx.fillStyle = pool; ctx.beginPath(); ctx.arc(0, 0, R * 0.9, 0, Math.PI * 2); ctx.fill();
      ctx.restore();

      // equator ring
      ctx.strokeStyle = `rgba(${INK},0.07)`; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.ellipse(cx, cy, R * 1.22, R * 1.22 * Math.abs(Math.sin(tiltX)) + 2, 0, 0, Math.PI * 2); ctx.stroke();

      for (const [a, b] of edges) {
        const A = proj[a], B = proj[b];
        const d = (A.depth + B.depth) / 2;
        ctx.strokeStyle = `rgba(${INK},${0.03 + d * d * 0.2})`;
        ctx.lineWidth = 0.5 + d * 0.6;
        ctx.beginPath(); ctx.moveTo(A.x, A.y); ctx.lineTo(B.x, B.y); ctx.stroke();
      }
      const order = proj.map((_, i) => i).sort((i, j) => proj[i].z - proj[j].z).reverse();
      for (const i of order) {
        const P = proj[i], p = pts[i];
        const rr = p.r * P.s * (0.6 + P.depth * 0.7);
        if (p.tint !== null) {
          const g = ctx.createRadialGradient(P.x, P.y, 0, P.x, P.y, rr * 5);
          g.addColorStop(0, `rgba(${AREA_RGB[p.tint]},${0.28 * P.depth})`); g.addColorStop(1, `rgba(${AREA_RGB[p.tint]},0)`);
          ctx.fillStyle = g; ctx.beginPath(); ctx.arc(P.x, P.y, rr * 5, 0, Math.PI * 2); ctx.fill();
          ctx.fillStyle = `rgba(${AREA_RGB[p.tint]},${0.35 + P.depth * 0.65})`;
        } else {
          ctx.fillStyle = `rgba(${INK},${0.12 + P.depth * 0.7})`;
        }
        ctx.beginPath(); ctx.arc(P.x, P.y, rr, 0, Math.PI * 2); ctx.fill();
      }

      if (!reduce) {
        if (now > nextPulse && pulses.length < 14) {
          const [a, b] = edges[Math.floor(Math.random() * edges.length)];
          pulses.push({ a, b, t: 0, speed: 0.0011 + Math.random() * 0.0012, rgb: AREA_RGB[Math.floor(Math.random() * 4)] });
          nextPulse = now + 90 + Math.random() * 220;
        }
        for (let k = pulses.length - 1; k >= 0; k--) {
          const pl = pulses[k];
          pl.t += pl.speed * dt;
          if (pl.t >= 1) { pulses.splice(k, 1); continue; }
          const A = proj[pl.a], B = proj[pl.b];
          const d = (A.depth + B.depth) / 2;
          const x = A.x + (B.x - A.x) * pl.t, y = A.y + (B.y - A.y) * pl.t;
          const t0 = Math.max(0, pl.t - 0.35);
          const gx = A.x + (B.x - A.x) * t0, gy = A.y + (B.y - A.y) * t0;
          const grad = ctx.createLinearGradient(gx, gy, x, y);
          grad.addColorStop(0, `rgba(${pl.rgb},0)`); grad.addColorStop(1, `rgba(${pl.rgb},${0.25 + d * 0.75})`);
          ctx.strokeStyle = grad; ctx.lineWidth = 1.2 + d * 1.2;
          ctx.beginPath(); ctx.moveTo(gx, gy); ctx.lineTo(x, y); ctx.stroke();
          ctx.fillStyle = `rgba(${pl.rgb},${0.3 + d * 0.7})`;
          ctx.beginPath(); ctx.arc(x, y, 1.4 + d * 1.4, 0, Math.PI * 2); ctx.fill();
        }
        raf = requestAnimationFrame(frame);
      }
    }

    const onMove = (e: PointerEvent) => {
      const r = canvas.getBoundingClientRect();
      const nx = (e.clientX - r.left) / r.width - 0.5, ny = (e.clientY - r.top) / r.height - 0.5;
      if (Math.abs(nx) > 1.2 || Math.abs(ny) > 1.2) return;
      tx = -0.32 + ny * 0.5; ty = nx * 0.6;
    };
    const ro = new ResizeObserver(() => { resize(); if (reduce) frame(performance.now()); });
    ro.observe(canvas.parentElement!);
    resize();
    if (reduce) frame(performance.now());
    else raf = requestAnimationFrame(frame);
    window.addEventListener("pointermove", onMove, { passive: true });
    return () => { cancelAnimationFrame(raf); ro.disconnect(); window.removeEventListener("pointermove", onMove); };
  }, [count]);

  return <canvas ref={ref} className="sphere" aria-hidden="true" />;
}
