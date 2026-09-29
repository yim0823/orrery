/* orrery — the map.
 *
 * The estate is drawn as an orrery: foundations at the core, the call layer at the rim.
 * Every ring is one layer of the stack, and a thing sits inside the wedge of whatever it
 * stands on, so "these two replicas are on one machine" is something you can see.
 *
 * The page computes nothing about consequence itself. Every blast radius and every
 * simulation comes from the engine — live from `orrery map`, or precomputed into the file
 * by `orrery map --export`. What the page adds is order of arrival: which failure reached
 * which entity through which edge, replayed as a wave.
 *
 * No dependencies and no build step, on purpose: this ships inside a Python package and
 * has to work in a browser with no network.
 */
(() => {
  'use strict';

  // ------------------------------------------------------------------ vocabulary --
  const FAM = {
    phys: { name: 'Physical', color: '#f3c677' },
    virt: { name: 'Virtual & cluster', color: '#5ce2c9' },
    data: { name: 'Data', color: '#d78bff' },
    work: { name: 'Workloads', color: '#8fa4ff' },
    edge: { name: 'Network & edge', color: '#6fd3ff' },
    ext: { name: 'External', color: '#c8ced8' },
  };
  // kind -> [family, tier]. The tier is the orbit: 0 is the core.
  const KIND = {
    site: ['phys', 0], rack: ['phys', 1], network_segment: ['edge', 1],
    host: ['phys', 2], storage: ['data', 2], vm: ['virt', 3], cluster: ['virt', 4],
    node: ['virt', 4], database: ['data', 5], queue: ['data', 5], service: ['work', 6],
    job: ['work', 6], load_balancer: ['edge', 7], dns: ['edge', 7], cdn: ['edge', 7],
    certificate: ['edge', 7], external: ['ext', 7],
  };
  const kindInfo = (k) => KIND[k] || ['ext', 9];
  const STRUCTURAL = new Set(['RUNS_ON', 'HOSTED_IN', 'MEMBER_OF', 'CONNECTS_TO']);
  const PARENT_PRIORITY = ['RUNS_ON', 'HOSTED_IN', 'MEMBER_OF', 'CONNECTS_TO'];
  const REL_COLOR = {
    RUNS_ON: '#f3c677', HOSTED_IN: '#f3c677', MEMBER_OF: '#5ce2c9', CONNECTS_TO: '#6fd3ff',
    DEPENDS_ON: '#8fa4ff', REACHED_VIA: '#6fd3ff',
  };
  const REL_VERB = {
    RUNS_ON: 'runs on', HOSTED_IN: 'hosted in', MEMBER_OF: 'member of',
    CONNECTS_TO: 'connects to', DEPENDS_ON: 'depends on', REACHED_VIA: 'reached via',
  };
  const REL_VERB_IN = {
    RUNS_ON: 'runs here', HOSTED_IN: 'hosted here', MEMBER_OF: 'member',
    CONNECTS_TO: 'attached', DEPENDS_ON: 'needs this', REACHED_VIA: 'reached via this',
  };
  const STATUS_COLOR = { down: '#ff4d61', degraded: '#ffb341', range: '#b69cff' };
  const WAVE_MS = 420;
  const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ------------------------------------------------------------------------ dom --
  const $ = (s, r = document) => r.querySelector(s);
  const app = $('#app');
  const canvas = $('#map');
  const ctx = canvas.getContext('2d');
  const stars = $('#stars');
  const tooltip = $('#tooltip');
  const inspector = $('#inspector');
  const hud = $('#hud');
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmtInt = (n) => n.toLocaleString('en-US');
  const pct = (x) => `${Math.round(x * 100)}%`;

  // ---------------------------------------------------------------------- state --
  const S = {
    world: null,
    nodes: [], byId: new Map(), edges: [], rings: [],
    cam: { x: 0, y: 0, k: 1 }, camTo: null,
    W: 0, H: 0, dpr: 1,
    hover: null, selected: null, preview: null,
    hidden: new Set(),
    result: null, // { mode: 'sim'|'blast', trigger, event, status: Map, wave: Map, causal: Set<edge>, start }
    elapsed: 0,
    t0: performance.now(),
    maxR: 400,
  };

  // --------------------------------------------------------------- data source --
  const Source = {
    snap: null,
    async world() {
      if (window.ORRERY_SNAPSHOT) {
        this.snap = window.ORRERY_SNAPSHOT;
        return { mode: 'snapshot', ...this.snap };
      }
      return getJSON('api/world');
    },
    async blast(id) {
      if (this.snap) return { root: id, impacted: (this.snap.blasts[id] || { impacted: [] }).impacted };
      return getJSON(`api/blast?id=${encodeURIComponent(id)}`);
    },
    async simulate(id, event, elapsed) {
      if (this.snap) {
        const tol = this.snap.tolerances;
        let bucket = 0;
        if (elapsed) for (const t of tol) if (elapsed >= t) bucket += 1;
        const rows = this.snap.sims[`${id}|${event}|${bucket}`] || [];
        return { trigger: id, event, elapsed_s: elapsed || null, effects: rows.map(([i, s, w]) => ({ id: i, status: s, why: w })) };
      }
      const q = new URLSearchParams({ id, event });
      if (elapsed) q.set('elapsed_s', String(elapsed));
      return getJSON(`api/simulate?${q}`);
    },
  };
  async function getJSON(url) {
    const r = await fetch(url, { headers: { Accept: 'application/json' } });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || `${r.status} ${r.statusText}`);
    return body;
  }

  // --------------------------------------------------------------------- layout --
  /* Rings by tier, wedges by ancestry.
   *
   * Each entity's parent is the first thing it stands on that sits on an inner ring. A
   * subtree gets an angular span proportional to its size, so everything on a host is
   * drawn inside that host's wedge, and everything on a rack inside the rack's. Entities
   * with nothing under or over them — a load balancer, an external provider, a cluster —
   * are placed at the mean angle of whatever they connect to. Then each ring is relaxed so
   * nothing overlaps.
   */
  function layout(world) {
    const nodes = world.entities.map((e) => {
      const [fam, tier] = kindInfo(e.kind);
      return {
        id: e.id, e, fam, tier, color: FAM[fam].color,
        out: [], in: [], parent: null, children: [],
        size: 3.4 + Math.min(10, 1.9 * Math.sqrt(e.reach || 0)) + (e.kind === 'site' ? 3 : 0),
        angle: 0, r: 0, x: 0, y: 0, ring: 0,
        appear: 0, placed: false,
      };
    });
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const edges = [];
    for (const r of world.relations) {
      const a = byId.get(r.src), b = byId.get(r.dst);
      if (!a || !b) continue;
      const e = { src: a, dst: b, kind: r.kind, soft: r.strength === 'soft', rel: r, seed: Math.random() };
      a.out.push(e); b.in.push(e); edges.push(e);
    }

    // rings: only tiers that exist, compressed
    const tiers = [...new Set(nodes.map((n) => n.tier))].sort((a, b) => a - b);
    const ringOf = new Map(tiers.map((t, i) => [t, i]));
    for (const n of nodes) n.ring = ringOf.get(n.tier);
    const ringNodes = tiers.map((_, i) => nodes.filter((n) => n.ring === i));
    const SPACING = 26;
    const radii = [];
    let prev = 0;
    ringNodes.forEach((list, i) => {
      const need = (list.length * SPACING) / (2 * Math.PI);
      let r;
      if (i === 0) r = list.length === 1 ? 0 : Math.max(70, need);
      else r = Math.max(prev + (prev === 0 ? 96 : 72), need);
      radii.push(r);
      prev = r;
    });

    // parents
    for (const n of nodes) {
      for (const kind of PARENT_PRIORITY) {
        const cands = n.out.filter((e) => e.kind === kind && e.dst.tier < n.tier).map((e) => e.dst);
        if (cands.length) {
          cands.sort((a, b) => (a.id < b.id ? -1 : 1));
          n.parent = cands[0];
          break;
        }
      }
      if (n.parent) n.parent.children.push(n);
    }
    const weight = new Map();
    const w = (n) => {
      if (weight.has(n)) return weight.get(n);
      weight.set(n, 1); // cycle guard
      const v = 1 + n.children.reduce((s, c) => s + w(c), 0);
      weight.set(n, v);
      return v;
    };
    const roots = nodes.filter((n) => !n.parent && n.children.length)
      .sort((a, b) => a.tier - b.tier || (a.id < b.id ? -1 : 1));
    const total = roots.reduce((s, n) => s + w(n), 0) || 1;
    const START = -Math.PI / 2;
    let cursor = START;
    const assign = (n, from, span) => {
      n.angle = from + span / 2; n.placed = true;
      const kids = [...n.children].sort((a, b) => a.tier - b.tier || (a.id < b.id ? -1 : 1));
      const kw = kids.reduce((s, c) => s + w(c), 0) || 1;
      // a parent keeps a sliver of its own wedge, so it is never hidden behind a child
      let c = from + span * (0.5 / w(n));
      const inner = span * (1 - 1 / w(n));
      for (const k of kids) {
        const sp = (inner * w(k)) / kw;
        if (!k.placed) assign(k, c, sp);
        c += sp;
      }
    };
    for (const r of roots) {
      const span = (2 * Math.PI * w(r)) / total;
      assign(r, cursor, span);
      cursor += span;
    }
    // floaters: circular mean of placed neighbours, a few rounds so chains settle
    for (let round = 0; round < 4; round++) {
      for (const n of nodes) {
        if (n.placed && round === 0) continue;
        if (n.placed && !n.floater) continue;
        let sx = 0, sy = 0, c = 0;
        for (const e of [...n.out, ...n.in]) {
          const o = e.src === n ? e.dst : e.src;
          if (!o.placed) continue;
          sx += Math.cos(o.angle); sy += Math.sin(o.angle); c++;
        }
        if (c) { n.angle = Math.atan2(sy, sx); n.placed = true; n.floater = true; }
      }
    }
    const orphans = nodes.filter((n) => !n.placed);
    orphans.forEach((n, i) => { n.angle = START + (2 * Math.PI * (i + 0.5)) / orphans.length; n.placed = true; });

    // relax each ring so bodies do not overlap
    ringNodes.forEach((list, i) => {
      const R = radii[i];
      if (R === 0 || list.length < 2) return;
      let gap = SPACING / R;
      if (gap * list.length > 2 * Math.PI) gap = (2 * Math.PI) / list.length;
      for (let it = 0; it < 60; it++) {
        list.sort((a, b) => norm(a.angle) - norm(b.angle));
        let moved = false;
        for (let j = 0; j < list.length; j++) {
          const a = list[j], b = list[(j + 1) % list.length];
          let d = norm(b.angle) - norm(a.angle);
          if (j === list.length - 1) d += 2 * Math.PI;
          if (d < gap) {
            const push = (gap - d) / 2 + 1e-4;
            a.angle -= push; b.angle += push; moved = true;
          }
        }
        if (!moved) break;
      }
    });

    for (const n of nodes) {
      n.r = radii[n.ring];
      n.x = Math.cos(n.angle) * n.r;
      n.y = Math.sin(n.angle) * n.r;
      n.appear = n.ring * 70 + Math.random() * 160;
    }
    for (const e of edges) shapeEdge(e);
    // A name is a label only where it names one thing: two racks and a host all called a1
    // would read as one entity drawn three times.
    const seen = new Map();
    for (const n of nodes) seen.set(n.e.name, (seen.get(n.e.name) || 0) + 1);
    for (const n of nodes) n.label = n.e.name && seen.get(n.e.name) === 1 && n.e.name.length < 24 ? n.e.name : n.id;

    const rings = tiers.map((t, i) => ({
      r: radii[i],
      label: [...new Set(ringNodes[i].map((n) => n.e.kind))].sort().map((k) => k.replace('_', ' ')).join(' · '),
      dir: i % 2 ? 1 : -1,
    }));
    return { nodes, byId, edges, rings, maxR: radii[radii.length - 1] || 200 };
  }
  function norm(a) { a %= 2 * Math.PI; return a < 0 ? a + 2 * Math.PI : a; }

  // A cubic between two bodies. Across rings it is a radial link (controls at the middle
  // radius); along one ring it bows toward the core, deeper the farther apart the ends.
  function shapeEdge(e) {
    const a = e.src, b = e.dst;
    let da = norm(b.angle - a.angle); if (da > Math.PI) da -= 2 * Math.PI;
    const same = Math.abs(a.r - b.r) < 1;
    let m = (a.r + b.r) / 2;
    if (same) m = a.r * (0.78 - 0.3 * Math.abs(da) / Math.PI);
    else if (!STRUCTURAL.has(e.kind)) m *= 0.92 - 0.2 * Math.abs(da) / Math.PI;
    const a1 = a.angle, a2 = a.angle + da;
    e.c1 = { x: Math.cos(a1) * m, y: Math.sin(a1) * m };
    e.c2 = { x: Math.cos(a2) * m, y: Math.sin(a2) * m };
    if (a.r === 0) e.c1 = { x: Math.cos(a2) * m * 0.5, y: Math.sin(a2) * m * 0.5 };
    if (b.r === 0) e.c2 = { x: Math.cos(a1) * m * 0.5, y: Math.sin(a1) * m * 0.5 };
  }
  function bez(e, t, p0, p3) {
    const u = 1 - t;
    const c1 = e.c1, c2 = e.c2;
    return {
      x: u * u * u * p0.x + 3 * u * u * t * c1.x + 3 * u * t * t * c2.x + t * t * t * p3.x,
      y: u * u * u * p0.y + 3 * u * u * t * c1.y + 3 * u * t * t * c2.y + t * t * t * p3.y,
    };
  }

  // ------------------------------------------------------------------- camera --
  function resize() {
    S.dpr = Math.min(2, window.devicePixelRatio || 1);
    S.W = app.clientWidth; S.H = app.clientHeight;
    for (const c of [canvas, stars]) {
      c.width = Math.round(S.W * S.dpr); c.height = Math.round(S.H * S.dpr);
    }
    drawStars();
  }
  function viewport() {
    const wide = S.W > 900;
    const left = wide ? 314 : 0;
    const right = wide && S.selected ? 394 : 0;
    const top = wide ? 64 : 58;
    const bottom = !wide && S.selected ? S.H * 0.62 : 0;
    return { left, right, top, bottom };
  }
  function fitCam() {
    const v = viewport();
    const w = S.W - v.left - v.right, h = S.H - v.top - v.bottom;
    const R = S.maxR + 70;
    const k = Math.max(0.05, Math.min(2.2, Math.min(w, h) / (2 * R)));
    return { k, x: v.left + w / 2 - S.W / 2, y: v.top + h / 2 - S.H / 2 };
  }
  function flyTo(n, k) {
    const v = viewport();
    const cx = v.left + (S.W - v.left - v.right) / 2, cy = v.top + (S.H - v.top - v.bottom) / 2;
    const kk = k || Math.max(S.cam.k, fitCam().k * 1.6);
    S.camTo = { k: kk, x: cx - S.W / 2 - n.x * kk, y: cy - S.H / 2 - n.y * kk };
  }
  // Frame a set of bodies: what a simulation reached, with the trigger.
  function fitTo(ids) {
    const pts = [...ids].map((id) => S.byId.get(id)).filter(Boolean);
    if (!pts.length) return;
    const v = viewport();
    const pad = 70;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of pts) { x0 = Math.min(x0, n.x); y0 = Math.min(y0, n.y); x1 = Math.max(x1, n.x); y1 = Math.max(y1, n.y); }
    const w = S.W - v.left - v.right - 2 * pad, h = S.H - v.top - v.bottom - 2 * pad - 50;
    const base = fitCam().k;
    const k = Math.max(base, Math.min(base * 2.4, w / Math.max(1, x1 - x0), h / Math.max(1, y1 - y0)));
    const cx = v.left + (S.W - v.left - v.right) / 2, cy = v.top + 50 + (S.H - v.top - 50 - v.bottom) / 2;
    S.camTo = { k, x: cx - S.W / 2 - ((x0 + x1) / 2) * k, y: cy - S.H / 2 - ((y0 + y1) / 2) * k };
  }
  const toScreen = (x, y) => ({ x: S.W / 2 + S.cam.x + x * S.cam.k, y: S.H / 2 + S.cam.y + y * S.cam.k });
  const toWorld = (sx, sy) => ({ x: (sx - S.W / 2 - S.cam.x) / S.cam.k, y: (sy - S.H / 2 - S.cam.y) / S.cam.k });

  function drawStars() {
    const g = stars.getContext('2d');
    g.setTransform(S.dpr, 0, 0, S.dpr, 0, 0);
    g.clearRect(0, 0, S.W, S.H);
    let seed = 7;
    const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
    const count = Math.round((S.W * S.H) / 2600);
    for (let i = 0; i < count; i++) {
      const x = rnd() * S.W, y = rnd() * S.H, r = rnd() ** 3 * 1.3 + 0.2;
      g.globalAlpha = 0.15 + rnd() * 0.55;
      g.fillStyle = rnd() > 0.85 ? '#ffe6b8' : rnd() > 0.7 ? '#c9d6ff' : '#ffffff';
      g.beginPath(); g.arc(x, y, r, 0, Math.PI * 2); g.fill();
    }
    g.globalAlpha = 1;
  }

  // glow sprites, one per colour, so a thousand halos cost a thousand drawImage calls
  const sprites = new Map();
  function glow(color) {
    let s = sprites.get(color);
    if (s) return s;
    s = document.createElement('canvas');
    s.width = s.height = 128;
    const g = s.getContext('2d');
    const grd = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    grd.addColorStop(0, hexA(color, 0.9));
    grd.addColorStop(0.18, hexA(color, 0.45));
    grd.addColorStop(0.45, hexA(color, 0.12));
    grd.addColorStop(1, hexA(color, 0));
    g.fillStyle = grd; g.fillRect(0, 0, 128, 128);
    sprites.set(color, s);
    return s;
  }
  function hexA(hex, a) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }
  const ease = (t) => 1 - Math.pow(1 - Math.min(1, Math.max(0, t)), 3);

  // ------------------------------------------------------------------- render --
  function frame(now) {
    requestAnimationFrame(frame);
    if (document.hidden) return;
    if (S.camTo) {
      const f = 0.14;
      S.cam.k += (S.camTo.k - S.cam.k) * f;
      S.cam.x += (S.camTo.x - S.cam.x) * f;
      S.cam.y += (S.camTo.y - S.cam.y) * f;
      if (Math.abs(S.camTo.k - S.cam.k) < 1e-4 && Math.abs(S.camTo.x - S.cam.x) < 0.3 && Math.abs(S.camTo.y - S.cam.y) < 0.3) S.camTo = null;
    }
    draw(now);
    updateHudCounts(now);
  }

  function nodeState(n, now) {
    const R = S.result;
    if (!R) return null;
    if (n.id === R.trigger) return { status: R.mode === 'blast' ? 'range' : R.status.get(n.id)?.status || R.event, wave: 0, hit: now >= R.start };
    if (R.mode === 'blast') {
      const hop = R.wave.get(n.id);
      if (hop == null) return { status: null };
      return { status: 'range', wave: hop, hit: now >= R.start + hop * WAVE_MS };
    }
    const st = R.status.get(n.id);
    if (!st || !st.status) return { status: null };
    const wv = R.wave.get(n.id) ?? 1;
    return { status: st.status, wave: wv, hit: now >= R.start + wv * WAVE_MS };
  }

  function draw(now) {
    const t = now - S.t0;
    const { k } = S.cam;
    ctx.setTransform(S.dpr, 0, 0, S.dpr, 0, 0);
    ctx.clearRect(0, 0, S.W, S.H);
    ctx.save();
    ctx.translate(S.W / 2 + S.cam.x, S.H / 2 + S.cam.y);
    ctx.scale(k, k);

    const focus = S.hover || S.selected;
    const R = S.result;
    const neigh = focus && !R ? neighbourhood(focus) : null;
    const preview = S.preview;

    // orbits
    const intro = ease(t / 1400);
    for (const [i, ring] of S.rings.entries()) {
      if (ring.r === 0) continue;
      const rr = ring.r * (0.6 + 0.4 * intro);
      ctx.globalAlpha = 0.9 * intro;
      ctx.strokeStyle = 'rgba(170,190,255,0.075)';
      ctx.lineWidth = 1 / k;
      ctx.beginPath(); ctx.arc(0, 0, rr, 0, Math.PI * 2); ctx.stroke();
      // the gear teeth: tick marks that turn, alternating direction by ring
      ctx.save();
      ctx.rotate(REDUCED ? 0 : (t / 1000) * 0.012 * ring.dir * (1 + i * 0.15));
      ctx.setLineDash([1.2 / k, (i % 2 ? 9 : 13) / k]);
      ctx.strokeStyle = 'rgba(243,198,119,0.22)';
      ctx.lineWidth = 5 / k;
      ctx.beginPath(); ctx.arc(0, 0, rr, 0, Math.PI * 2); ctx.stroke();
      ctx.restore();
      ctx.setLineDash([]);
      // ring label, upright at the top of the orbit
      if (k * rr > 60) {
        ctx.fillStyle = 'rgba(160,175,215,0.42)';
        ctx.font = `600 ${9.5 / k}px "JetBrains Mono", "SF Mono", ui-monospace, Menlo, monospace`;
        ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
        const label = ring.label.toUpperCase().split('').join(' ');
        ctx.fillText(label, 0, -rr - 7 / k);
      }
    }
    ctx.globalAlpha = 1;

    // edges
    const hiddenEdge = (e) => S.hidden.has(e.src.e.kind) || S.hidden.has(e.dst.e.kind);
    ctx.lineCap = 'round';
    // a dense map is a haze of edges; thin them so the bodies stay legible
    const density = Math.min(1, Math.sqrt(500 / Math.max(1, S.edges.length)));
    for (const e of S.edges) {
      if (hiddenEdge(e)) continue;
      const pa = pos(e.src, t), pb = pos(e.dst, t);
      if (pa.p < 0.05 || pb.p < 0.05) continue;
      let alpha = (STRUCTURAL.has(e.kind) ? 0.16 : 0.42) * density;
      let width = STRUCTURAL.has(e.kind) ? 0.9 : 1.2;
      let color = REL_COLOR[e.kind] || '#8fa4ff';
      let active = false;
      if (R) {
        const c = R.causal.get(e);
        if (c) {
          const hitAt = R.start + c.wave * WAVE_MS;
          const p = Math.min(1, Math.max(0, (now - (hitAt - WAVE_MS)) / WAVE_MS));
          if (p <= 0) { alpha *= 0.2 / density; }
          else {
            active = true; alpha = 0.35 + 0.55 * p; width = 1.9;
            color = STATUS_COLOR[c.status] || STATUS_COLOR.range;
          }
        } else alpha *= 0.13;
      } else if (neigh) {
        if (neigh.edges.has(e)) { alpha = 0.95; width = 1.8; active = true; } else alpha *= 0.18;
      } else if (preview) {
        alpha *= preview.edges.has(e) ? 2.2 : 0.3;
      }
      ctx.globalAlpha = Math.min(1, alpha) * Math.min(pa.p, pb.p);
      ctx.strokeStyle = color;
      ctx.lineWidth = width / Math.sqrt(k) ;
      if (e.soft) ctx.setLineDash([4 / k, 4 / k]);
      ctx.beginPath();
      ctx.moveTo(pa.x, pa.y);
      if (pa.p < 1 || pb.p < 1) ctx.lineTo(pb.x, pb.y);
      else ctx.bezierCurveTo(e.c1.x, e.c1.y, e.c2.x, e.c2.y, pb.x, pb.y);
      ctx.stroke();
      if (e.soft) ctx.setLineDash([]);

      // particles along live edges: consequence travels from the thing that failed to what needs it
      if (active && pa.p >= 1 && pb.p >= 1 && !REDUCED) {
        const c = R && R.causal.get(e);
        const fromDst = c ? c.fromDst : true;
        const n = 3;
        ctx.fillStyle = color;
        for (let i = 0; i < n; i++) {
          let u = ((t / (c ? 900 : 1600)) + i / n + e.seed) % 1;
          if (fromDst) u = 1 - u;
          const p = bez(e, u, e.src, e.dst);
          ctx.globalAlpha = 0.9 * Math.sin(Math.PI * (fromDst ? 1 - u : u));
          ctx.beginPath(); ctx.arc(p.x, p.y, 1.9 / Math.sqrt(k), 0, Math.PI * 2); ctx.fill();
        }
      }
    }
    ctx.globalAlpha = 1;

    // shockwaves from the trigger
    if (R && !REDUCED) {
      const tn = S.byId.get(R.trigger);
      const since = now - R.start;
      const col = R.mode === 'blast' ? STATUS_COLOR.range : STATUS_COLOR[R.event] || STATUS_COLOR.down;
      for (let i = 0; i < 3; i++) {
        const s = since - i * 260;
        if (s < 0 || s > 2400) continue;
        const p = s / 2400;
        ctx.globalAlpha = (1 - p) * 0.55;
        ctx.strokeStyle = col;
        ctx.lineWidth = (2.2 * (1 - p) + 0.3) / k;
        ctx.beginPath(); ctx.arc(tn.x, tn.y, 8 + ease(p) * S.maxR * 0.9, 0, Math.PI * 2); ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }

    // bodies
    const labelAll = S.nodes.length <= 90 || k > 1.5;
    const labels = [];
    for (const n of S.nodes) {
      if (S.hidden.has(n.e.kind)) continue;
      const p = pos(n, t);
      if (p.p <= 0) continue;
      const st = nodeState(n, now);
      let alpha = 1;
      let color = n.color;
      let halo = 1;
      let ring = null;
      if (st) {
        if (st.status && st.hit) {
          color = STATUS_COLOR[st.status];
          const since = now - (R.start + st.wave * WAVE_MS);
          halo = 1.6 + (since < 600 ? (1 - since / 600) * 2.5 : 0) + (st.status === 'down' && !REDUCED ? 0.35 * Math.sin(t / 260 + n.seedPhase) : 0);
          if (R.flash && R.flash.has(n.id) && now - R.flash.get(n.id) < 1500) halo += 2 * (1 - (now - R.flash.get(n.id)) / 1500);
        } else alpha = st.status ? 0.5 : 0.18;
      } else if (neigh) {
        alpha = neigh.nodes.has(n) ? 1 : 0.2;
      } else if (preview) {
        alpha = preview.nodes.has(n) ? 1 : 0.28;
        if (preview.nodes.has(n) && n !== preview.root) ring = STATUS_COLOR.range;
      }
      const size = n.size * (n === S.hover ? 1.25 : 1);
      ctx.globalAlpha = alpha * p.p;
      const g = glow(color);
      const gs = size * 5.2 * halo;
      ctx.drawImage(g, p.x - gs / 2, p.y - gs / 2, gs, gs);
      drawBody(n, p.x, p.y, size, color, alpha * p.p);
      if (ring || n === S.selected || (R && n.id === R.trigger)) {
        ctx.globalAlpha = alpha * p.p;
        ctx.strokeStyle = n === S.selected ? '#ffffff' : ring || color;
        ctx.lineWidth = 1.4 / k;
        ctx.beginPath(); ctx.arc(p.x, p.y, size + 4.5 / Math.sqrt(k), 0, Math.PI * 2); ctx.stroke();
        if (n === S.selected && !REDUCED) {
          ctx.save();
          ctx.translate(p.x, p.y); ctx.rotate(t / 1800);
          ctx.setLineDash([3 / k, 5 / k]);
          ctx.globalAlpha = 0.6 * alpha;
          ctx.beginPath(); ctx.arc(0, 0, size + 10 / Math.sqrt(k), 0, Math.PI * 2); ctx.stroke();
          ctx.restore();
          ctx.setLineDash([]);
        }
      }
      const emphasised = n === focus || (neigh && neigh.nodes.has(n)) || (st && st.status && st.hit) || (preview && preview.root === n);
      if (p.p >= 1 && (labelAll ? alpha > 0.3 : emphasised)) labels.push([n, p, alpha, st]);
    }
    ctx.globalAlpha = 1;

    // labels on top, outward from the core so they read like a chart
    const mono = '"JetBrains Mono", "SF Mono", ui-monospace, Menlo, monospace';
    ctx.textBaseline = 'middle';
    for (const [n, p, alpha, st] of labels) {
      const fs = Math.max(9.5, Math.min(12, 10.5 * Math.sqrt(k))) / k;
      const ang = n.r === 0 ? Math.PI / 2 : n.angle;
      const off = n.size + 7 / k;
      const lx = p.x + Math.cos(ang) * off, ly = p.y + Math.sin(ang) * off;
      const right = Math.cos(ang) >= -0.15;
      ctx.textAlign = n.r === 0 ? 'center' : right ? 'left' : 'right';
      const txt = n.label;
      ctx.font = `${n === S.selected || n === S.hover ? 600 : 500} ${fs}px ${mono}`;
      ctx.globalAlpha = Math.min(1, alpha + 0.15);
      ctx.lineWidth = 3.2 / k; ctx.strokeStyle = 'rgba(5,7,13,0.9)';
      const ty = n.r === 0 ? ly + fs * 0.4 : ly;
      ctx.strokeText(txt, lx, ty);
      ctx.fillStyle = st && st.status && st.hit ? STATUS_COLOR[st.status] : n === focus ? '#ffffff' : 'rgba(220,228,245,0.82)';
      ctx.fillText(txt, lx, ty);
      if (S.result && S.result.mode === 'blast' && st && st.status && st.hit && n.id !== S.result.trigger) {
        ctx.font = `600 ${fs * 0.82}px ${mono}`;
        ctx.fillStyle = STATUS_COLOR.range;
        ctx.textAlign = 'center';
        ctx.strokeText(`${st.wave}`, p.x, p.y - n.size - fs * 0.9);
        ctx.fillText(`${st.wave}`, p.x, p.y - n.size - fs * 0.9);
      }
    }
    ctx.globalAlpha = 1;
    ctx.restore();
  }

  function drawBody(n, x, y, size, color, alpha) {
    const k = S.cam.k;
    ctx.globalAlpha = alpha;
    const kind = n.e.kind;
    if (kind === 'site') {
      // a sun: a corona of short rays
      ctx.strokeStyle = color; ctx.lineWidth = 1.2 / k;
      for (let i = 0; i < 12; i++) {
        const a = (i / 12) * Math.PI * 2 + (REDUCED ? 0 : (performance.now() - S.t0) / 9000);
        ctx.beginPath();
        ctx.moveTo(x + Math.cos(a) * (size + 2), y + Math.sin(a) * (size + 2));
        ctx.lineTo(x + Math.cos(a) * (size + 5.5), y + Math.sin(a) * (size + 5.5));
        ctx.stroke();
      }
    }
    const spr = bodySprite(kind, color);
    const d = (size + BODY_PAD * size / BODY_R) * 2;
    ctx.drawImage(spr, x - d / 2, y - d / 2, d, d);
  }

  // Bodies are drawn once per kind and colour at a fixed radius and scaled on use: a
  // gradient built per body per frame was most of the frame time on a thousand entities.
  const BODY_R = 32, BODY_PAD = 8;
  const bodies = new Map();
  function bodySprite(kind, color) {
    const key = kind + color;
    let c = bodies.get(key);
    if (c) return c;
    c = document.createElement('canvas');
    const D = (BODY_R + BODY_PAD) * 2;
    c.width = c.height = D;
    const g = c.getContext('2d');
    const x = D / 2, y = D / 2, size = BODY_R;
    const grd = g.createRadialGradient(x - size * 0.35, y - size * 0.35, size * 0.1, x, y, size);
    grd.addColorStop(0, '#ffffff');
    grd.addColorStop(0.35, color);
    grd.addColorStop(1, hexA(color, 0.55));
    g.beginPath();
    if (kind === 'external') {
      g.strokeStyle = color; g.lineWidth = 5;
      g.arc(x, y, size - 3, 0, Math.PI * 2); g.stroke();
      g.fillStyle = hexA(color, 0.25); g.fill();
      bodies.set(key, c);
      return c;
    }
    g.fillStyle = grd;
    if (kind === 'rack' || kind === 'host' || kind === 'storage') roundRect(g, x - size, y - size, size * 2, size * 2, size * 0.35);
    else if (['load_balancer', 'dns', 'cdn', 'network_segment', 'certificate'].includes(kind)) diamond(g, x, y, size * 1.12);
    else g.arc(x, y, size, 0, Math.PI * 2);
    g.fill();
    if (kind === 'database' || kind === 'queue' || kind === 'cluster') {
      g.strokeStyle = hexA(color, 0.7); g.lineWidth = 3;
      g.beginPath(); g.arc(x, y, size + 6, 0, Math.PI * 2); g.stroke();
    }
    bodies.set(key, c);
    return c;
  }
  function roundRect(g, x, y, w, h, r) {
    g.moveTo(x + r, y); g.arcTo(x + w, y, x + w, y + h, r); g.arcTo(x + w, y + h, x, y + h, r);
    g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath();
  }
  function diamond(g, x, y, s) { g.moveTo(x, y - s); g.lineTo(x + s, y); g.lineTo(x, y + s); g.lineTo(x - s, y); g.closePath(); }

  // entrance: every body flies out from the core to its orbit
  function pos(n, t) {
    if (REDUCED) return { x: n.x, y: n.y, p: 1 };
    const p = ease((t - 200 - n.appear) / 1100);
    if (p >= 1) return { x: n.x, y: n.y, p: 1 };
    const a = n.angle - (1 - p) * 0.9;
    const r = n.r * p;
    return { x: Math.cos(a) * r, y: Math.sin(a) * r, p };
  }

  function neighbourhood(n) {
    if (n._nb) return n._nb;
    const nodes = new Set([n]), edges = new Set();
    for (const e of n.out) { nodes.add(e.dst); edges.add(e); }
    for (const e of n.in) { nodes.add(e.src); edges.add(e); }
    n._nb = { nodes, edges };
    return n._nb;
  }

  // What goes with it, structurally — the same walk `spof` ranks by.
  function reachOf(n) {
    const nodes = new Set([n]), edges = new Set();
    const q = [n];
    while (q.length) {
      const cur = q.shift();
      const carries = new Set(cur.e.carries || []);
      for (const e of cur.in) {
        if (!carries.has(e.kind)) continue;
        edges.add(e);
        if (!nodes.has(e.src)) { nodes.add(e.src); q.push(e.src); }
      }
    }
    return { root: n, nodes, edges };
  }

  // ------------------------------------------------------------ consequence --
  /* The engine says what happened to each entity. This works out the order: breadth-first
   * from the trigger through the edges consequence travels on, restricted to the entities
   * the engine says were affected. Membership is followed upward too — a quorum losing its
   * members is how an etcd dies without anything under it dying. */
  function waves(trigger, affected) {
    const wave = new Map([[trigger, 0]]);
    const causal = new Map();
    const q = [trigger];
    while (q.length) {
      const id = q.shift();
      const n = S.byId.get(id);
      const w = wave.get(id);
      for (const e of n.in) {
        const o = e.src.id;
        if (!affected.has(o)) continue;
        if (!wave.has(o)) { wave.set(o, w + 1); q.push(o); }
        if (wave.get(o) === w + 1 && !causal.has(e)) causal.set(e, { wave: w + 1, fromDst: true, to: o });
      }
      for (const e of n.out) {
        if (e.kind !== 'MEMBER_OF') continue;
        const o = e.dst.id;
        if (!affected.has(o)) continue;
        if (!wave.has(o)) { wave.set(o, w + 1); q.push(o); }
        if (wave.get(o) === w + 1 && !causal.has(e)) causal.set(e, { wave: w + 1, fromDst: false, to: o });
      }
    }
    const max = Math.max(0, ...wave.values());
    for (const id of affected) if (!wave.has(id)) wave.set(id, max + 1);
    return { wave, causal };
  }

  async function simulate(id, event, { replay = true } = {}) {
    let res;
    try { res = await Source.simulate(id, event, S.elapsed || null); } catch (err) { return toast(err.message); }
    const status = new Map(res.effects.filter((e) => e.status).map((e) => [e.id, e]));
    const affected = new Set(status.keys());
    const { wave, causal } = waves(id, affected);
    for (const [e, c] of causal) c.status = status.get(c.to)?.status || 'degraded';
    const prev = S.result;
    const same = prev && prev.mode === 'sim' && prev.trigger === id && prev.event === event && !replay;
    const flash = new Map();
    const now = performance.now();
    if (same) {
      for (const [eid, st] of status) if (prev.status.get(eid)?.status !== st.status) flash.set(eid, now);
    }
    S.result = { mode: 'sim', trigger: id, event, status, wave, causal, effects: res.effects, flash, start: same ? now - 1e6 : now + 80 };
    for (const n of S.nodes) n.seedPhase = n.seedPhase ?? Math.random() * 6;
    renderHud();
    select(S.byId.get(id), { keepResult: true, fly: false });
    if (!same) fitTo(wave.keys());
  }

  async function blast(id) {
    let res;
    try { res = await Source.blast(id); } catch (err) { return toast(err.message); }
    const wave = new Map(res.impacted.map((x) => [x.id, x.hop]));
    const causal = new Map();
    for (const x of res.impacted) {
      const path = x.path || [];
      const a = path[path.length - 2], b = path[path.length - 1];
      const na = S.byId.get(a);
      if (!na) continue;
      const e = na.in.find((ed) => ed.src.id === b);
      if (e) causal.set(e, { wave: x.hop, fromDst: true, status: 'range', to: b });
    }
    wave.set(id, 0);
    S.result = { mode: 'blast', trigger: id, event: 'blast', status: new Map(), wave, causal, impacted: res.impacted, flash: new Map(), start: performance.now() + 80 };
    renderHud();
    select(S.byId.get(id), { keepResult: true, fly: false });
    fitTo(wave.keys());
  }

  function clearResult() {
    S.result = null;
    hud.hidden = true;
    if (S.selected) renderInspector();
  }

  // The chain from the trigger to one entity, for "why is this down".
  function chainTo(id) {
    const R = S.result;
    if (!R || !R.wave.has(id)) return [];
    const out = [id];
    let cur = id, guard = 0;
    while (cur !== R.trigger && guard++ < 64) {
      let next = null;
      for (const [e, c] of R.causal) {
        if (c.to !== cur) continue;
        next = c.fromDst ? e.dst.id : e.src.id;
        break;
      }
      if (!next) break;
      out.unshift(next);
      cur = next;
    }
    return out;
  }

  // ------------------------------------------------------------------- HUD --
  function renderHud() {
    const R = S.result;
    if (!R) { hud.hidden = true; return; }
    const n = S.byId.get(R.trigger);
    const isSim = R.mode === 'sim';
    const col = isSim ? STATUS_COLOR[R.event] : STATUS_COLOR.range;
    hud.style.setProperty('--hc', col);
    const counts = isSim
      ? `<div class="hud-count" style="--cc:${STATUS_COLOR.down}"><b data-count="down">0</b><span>down</span></div>
         <div class="hud-count" style="--cc:${STATUS_COLOR.degraded}"><b data-count="degraded">0</b><span>degraded</span></div>
         <div class="hud-count" style="--cc:#a4adc4"><b data-count="ok">${fmtInt(S.nodes.length)}</b><span>untouched</span></div>`
      : `<div class="hud-count" style="--cc:${STATUS_COLOR.range}"><b data-count="range">0</b><span>in range</span></div>
         <div class="hud-count" style="--cc:#a4adc4"><b>${fmtInt(Math.max(0, ...R.wave.values()))}</b><span>hops</span></div>`;
    hud.innerHTML = `
      <div class="hud-mode">${isSim ? 'Simulation' : 'Blast radius'}</div>
      <div class="hud-target">${esc(n.id)}<small>${isSim ? esc(R.event) + (S.elapsed ? ' · t+' + fmtDur(S.elapsed) : '') : 'structural'}</small></div>
      <div class="hud-counts">${counts}</div>
      <button class="hud-btn" type="button" data-hud="replay" aria-label="Replay" title="Replay">
        <svg viewBox="0 0 16 16"><path d="M2.5 8a5.5 5.5 0 1 0 1.6-3.9"/><path d="M2.5 2.5v3h3"/></svg></button>
      <button class="hud-btn" type="button" data-hud="clear" aria-label="Clear (Esc)" title="Clear (Esc)">
        <svg viewBox="0 0 16 16"><path d="M4 4l8 8M12 4l-8 8"/></svg></button>`;
    hud.hidden = false;
  }
  function updateHudCounts(now) {
    const R = S.result;
    if (!R || hud.hidden) return;
    const c = { down: 0, degraded: 0, range: 0 };
    if (R.mode === 'sim') {
      for (const [id, st] of R.status) {
        const w = R.wave.get(id) ?? 0;
        if (now >= R.start + w * WAVE_MS && c[st.status] != null) c[st.status]++;
      }
      c.ok = S.nodes.length - c.down - c.degraded;
    } else {
      for (const [id, w] of R.wave) if (id !== R.trigger && now >= R.start + w * WAVE_MS) c.range++;
    }
    for (const el of hud.querySelectorAll('[data-count]')) {
      const v = fmtInt(c[el.dataset.count]);
      if (el.textContent !== v) el.textContent = v;
    }
  }
  hud.addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-hud]');
    if (!b) return;
    if (b.dataset.hud === 'clear') clearResult();
    else if (S.result) {
      if (S.result.mode === 'sim') simulate(S.result.trigger, S.result.event);
      else blast(S.result.trigger);
    }
  });

  // ------------------------------------------------------------- inspector --
  function select(n, { keepResult = false, fly = true } = {}) {
    if (!keepResult && S.result && n && S.result.trigger !== n.id && !S.result.wave.has(n.id)) {
      // clicking something the simulation never reached ends the simulation
      clearResult();
    }
    const changed = S.selected !== n;
    S.selected = n;
    app.classList.toggle('has-inspector', !!n);
    if (!n) { inspector.hidden = true; return; }
    renderInspector();
    if (fly && changed) flyTo(n);
  }

  function renderInspector() {
    const n = S.selected;
    if (!n) return;
    const e = n.e;
    const R = S.result;
    const findings = (S.world.audit.findings || []).filter((f) => f.id === e.id);
    const st = R && R.mode === 'sim' ? R.status.get(e.id) : null;
    const isTrigger = R && R.trigger === e.id;
    const deps = n.out.length, needed = n.in.length;

    let html = `
    <div class="insp-scroll">
      <div class="insp-hero" style="--c:${n.color}">
        <button class="insp-close" type="button" data-act="close" aria-label="Close">
          <svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 4l8 8M12 4l-8 8"/></svg></button>
        <div class="kind-chip">${esc(e.kind.replace('_', ' '))}</div>
        <h2 class="insp-name">${esc(e.name || e.id)}</h2>
        <div class="insp-id">${esc(e.id)}</div>
        ${e.status && e.status !== 'up' ? `<div class="insp-status" style="--sc:${STATUS_COLOR[e.status] || '#a4adc4'}">recorded ${esc(e.status)}</div>` : ''}
        ${st && st.status ? `<div class="insp-status" style="--sc:${STATUS_COLOR[st.status]}">${esc(st.status)} in this simulation</div>` : ''}
        <div class="metrics">
          <div><b>${fmtInt(e.reach || 0)}</b><span>go with it</span></div>
          <div><b>${pct(e.share || 0)}</b><span>of the estate</span></div>
          <div><b>${deps}<span style="color:var(--text-3);font-size:13px"> / </span>${needed}</b><span>on / needed by</span></div>
        </div>
      </div>
      <div class="actions">
        <button class="btn primary" type="button" data-act="down">
          <svg viewBox="0 0 16 16"><path d="M8 1.5v6M4.2 3.8a5.5 5.5 0 1 0 7.6 0"/></svg>
          Simulate outage <kbd>S</kbd></button>
        <button class="btn warn" type="button" data-act="degraded">
          <svg viewBox="0 0 16 16"><path d="M1.5 4.5l4.5 5 3-3 5.5 5"/></svg>
          Degrade <kbd>D</kbd></button>
        <button class="btn ghost" type="button" data-act="blast">
          <svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="2"/><circle cx="8" cy="8" r="5.5" stroke-dasharray="2 2"/></svg>
          Blast radius <kbd>B</kbd></button>
      </div>`;

    if (R && isTrigger && R.mode === 'sim') html += consequenceSection(R);
    else if (R && isTrigger && R.mode === 'blast') html += blastSection(R);
    else if (R && R.wave.has(e.id)) {
      const chain = chainTo(e.id);
      const col = R.mode === 'sim' ? STATUS_COLOR[st?.status] || STATUS_COLOR.degraded : STATUS_COLOR.range;
      html += `<div class="sec"><div class="callout" style="--cc:${col}">
        <b>${R.mode === 'sim' ? esc((st?.status || '').toUpperCase()) + ' — ' + esc(st?.why || 'passthrough') : 'In range at hop ' + R.wave.get(e.id)}</b>
        how it got here, from <code>${esc(R.trigger)}</code>:
        <div class="chain">${chain.map((id) => `<button type="button" data-go="${esc(id)}">${esc(id)}</button>`).join('<i>→</i>')}</div>
      </div></div>`;
    }

    if (findings.length) {
      html += `<div class="sec"><h4 class="sec-title">Worth a look</h4>${findings.map((f) => `
        <div class="callout" style="--cc:var(--degraded)"><b>${esc(f.check)}</b>${esc(f.detail)}</div>`).join('')}</div>`;
    }

    const relList = (edges, dir) => {
      if (!edges.length) return `<div class="none">${dir === 'out' ? 'Stands on nothing recorded.' : 'Nothing recorded needs it.'}</div>`;
      return `<div class="rels">${edges
        .slice().sort((a, b) => (a.kind < b.kind ? -1 : a.kind > b.kind ? 1 : 0))
        .map((ed) => {
          const o = dir === 'out' ? ed.dst : ed.src;
          const tags = [ed.soft ? 'soft' : '', ed.rel.attrs && ed.rel.attrs.tolerance_s ? `holds ${fmtDur(+ed.rel.attrs.tolerance_s)}` : ''].filter(Boolean);
          return `<button class="rel" type="button" data-go="${esc(o.id)}" style="--rc:${REL_COLOR[ed.kind] || '#8fa4ff'};--nc:${o.color}">
            <span class="rel-kind">${esc((dir === 'out' ? REL_VERB : REL_VERB_IN)[ed.kind] || ed.kind)}</span>
            <span class="rel-id">${esc(o.id)}</span>
            <span class="rel-tags">${tags.map((t) => `<span class="rel-tag">${esc(t)}</span>`).join('')}</span>
          </button>`;
        }).join('')}</div>`;
    };
    html += `<div class="sec"><h4 class="sec-title">Stands on <small>${deps}</small></h4>${relList(n.out, 'out')}</div>`;
    html += `<div class="sec"><h4 class="sec-title">Needed by <small>${needed}</small></h4>${relList(n.in, 'in')}</div>`;

    const attrs = Object.entries(e.attrs || {});
    if (attrs.length) {
      html += `<div class="sec"><h4 class="sec-title">Attributes</h4><dl class="kv">${attrs
        .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(typeof v === 'object' ? JSON.stringify(v) : v)}</dd>`).join('')}</dl></div>`;
    }
    html += `<div class="sec"><h4 class="sec-title">Provenance</h4>${
      e.sources && e.sources.length
        ? `<dl class="kv">${e.sources.map((s) => `<dt>source</dt><dd>${esc(s)}</dd>`).join('')}</dl>`
        : '<div class="callout" style="--cc:var(--degraded)"><b>no provenance</b>Nothing says where this came from — when two sources disagree there is no way to choose.</div>'
    }</div>`;
    html += '</div>';
    const scroll = inspector.querySelector('.insp-scroll');
    const keep = scroll && inspector.dataset.id === e.id ? scroll.scrollTop : 0;
    inspector.innerHTML = html;
    inspector.dataset.id = e.id;
    inspector.hidden = false;
    inspector.querySelector('.insp-scroll').scrollTop = keep;
    wireSlider();
  }

  const STEPS_BASE = [0, 60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400];
  function clockSteps() {
    return [...new Set([...STEPS_BASE, ...(S.world.tolerances || [])])].sort((a, b) => a - b);
  }
  function fmtDur(s) {
    if (!s) return '0';
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.round(s / 60)}m`;
    const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
    return m ? `${h}h${m}m` : `${h}h`;
  }

  function consequenceSection(R) {
    const steps = clockSteps();
    const idx = Math.max(0, steps.indexOf(S.elapsed));
    const tol = S.world.tolerances || [];
    const pctOf = (i) => (steps.length > 1 ? (i / (steps.length - 1)) * 100 : 0);
    const ticks = tol.map((t) => `<div class="slider-tick" style="left:${pctOf(steps.indexOf(t))}%" title="a soft dependency turns hard at ${fmtDur(t)}"></div>`).join('');
    const clock = tol.length || S.elapsed ? `
      <div class="clock">
        <div class="clock-head"><span>How long has it been down?</span><b>t + ${fmtDur(S.elapsed)}</b></div>
        <div class="slider">
          <div class="slider-track"></div>
          <div class="slider-fill" style="width:${pctOf(idx)}%"></div>
          ${ticks}
          <input type="range" min="0" max="${steps.length - 1}" step="1" value="${idx}" aria-label="Elapsed time since the failure" aria-valuetext="${fmtDur(S.elapsed)}">
          <div class="slider-thumb" style="left:${pctOf(idx)}%"></div>
        </div>
        <div class="slider-scale"><span>now</span><span>${fmtDur(steps[steps.length - 1])}</span></div>
        <div class="clock-note">${tol.length ? `<em>|</em> marks where a soft dependency runs out of patience and turns hard.` : ''}</div>
      </div>` : '';

    const groups = { down: [], degraded: [] };
    for (const e of R.effects) if (e.status && groups[e.status] && e.id !== R.trigger) groups[e.status].push(e);
    const byWave = (a, b) => (R.wave.get(a.id) ?? 0) - (R.wave.get(b.id) ?? 0);
    const list = (key, label) => {
      const items = groups[key].sort(byWave);
      if (!items.length) return '';
      return `<div class="fx-group" style="--sc:${STATUS_COLOR[key]}"><h5>${label} <span>${items.length}</span></h5>${items.map((x, i) => `
        <button class="fx${R.flash.has(x.id) ? ' flash' : ''}" type="button" data-go="${esc(x.id)}" style="animation-delay:${Math.min(i * 40, 600)}ms">
          <code>${esc(x.id)}</code><small>${esc(S.byId.get(x.id)?.e.kind || '')} · hop ${R.wave.get(x.id) ?? '?'}</small>
          <span>${esc(x.why || 'passthrough')}</span></button>`).join('')}</div>`;
    };
    const untouched = S.nodes.length - R.status.size;
    const body = groups.down.length || groups.degraded.length
      ? list('down', 'Down') + list('degraded', 'Degraded')
      : `<div class="callout" style="--cc:var(--ok)"><b>Nothing else is affected</b>Every dependent has another place to run or another way in.</div>`;
    return `<div class="sec"><h4 class="sec-title">Consequence <small>${untouched} untouched</small></h4>${clock}${body}</div>`;
  }

  function blastSection(R) {
    const byHop = new Map();
    for (const x of R.impacted) { if (!byHop.has(x.hop)) byHop.set(x.hop, []); byHop.get(x.hop).push(x); }
    if (!R.impacted.length) return `<div class="sec"><div class="callout" style="--cc:var(--ok)"><b>Nothing is in range</b>Nothing recorded stands on this.</div></div>`;
    return `<div class="sec"><h4 class="sec-title">In range <small>${R.impacted.length} of ${S.nodes.length - 1}</small></h4>
      <div class="callout" style="--cc:var(--range)"><b>Structural, not predicted</b>What could be reached if this goes. Simulate to see what actually dies.</div>
      ${[...byHop.entries()].map(([hop, items]) => `<div class="fx-group" style="--sc:${STATUS_COLOR.range}"><h5>hop ${hop} <span>${items.length}</span></h5>
        ${items.map((x) => `<button class="fx" type="button" data-go="${esc(x.id)}"><code>${esc(x.id)}</code><small>${esc(x.kind)}</small></button>`).join('')}</div>`).join('')}
    </div>`;
  }

  let sliderTimer = 0;
  function wireSlider() {
    const input = inspector.querySelector('.slider input');
    if (!input) return;
    const steps = clockSteps();
    input.addEventListener('input', () => {
      const i = +input.value;
      S.elapsed = steps[i];
      const p = (i / (steps.length - 1)) * 100;
      inspector.querySelector('.slider-thumb').style.left = `${p}%`;
      inspector.querySelector('.slider-fill').style.width = `${p}%`;
      inspector.querySelector('.clock-head b').textContent = `t + ${fmtDur(S.elapsed)}`;
      clearTimeout(sliderTimer);
      sliderTimer = setTimeout(() => {
        if (S.result && S.result.mode === 'sim') simulate(S.result.trigger, S.result.event, { replay: false });
      }, 90);
    });
  }

  inspector.addEventListener('click', (ev) => {
    const go = ev.target.closest('[data-go]');
    if (go) {
      const n = S.byId.get(go.dataset.go);
      if (n) { select(n, { keepResult: true }); }
      return;
    }
    const act = ev.target.closest('[data-act]');
    if (!act || !S.selected) return;
    const a = act.dataset.act;
    if (a === 'close') { clearResult(); select(null); }
    else if (a === 'blast') blast(S.selected.id);
    else simulate(S.selected.id, a);
  });
  inspector.addEventListener('mouseover', (ev) => {
    const go = ev.target.closest('[data-go]');
    S.hover = go ? S.byId.get(go.dataset.go) || null : S.hover;
  });
  inspector.addEventListener('mouseleave', () => { S.hover = null; });

  // ------------------------------------------------------------------ rail --
  function renderRail() {
    const w = S.world;
    const count = new Map();
    for (const e of w.entities) count.set(e.kind, (count.get(e.kind) || 0) + 1);
    const byFam = new Map();
    for (const [kind] of count) {
      const fam = kindInfo(kind)[0];
      if (!byFam.has(fam)) byFam.set(fam, []);
      byFam.get(fam).push(kind);
    }
    $('#legend').innerHTML = Object.keys(FAM).filter((f) => byFam.has(f)).map((f) => `
      <div class="fam" style="--c:${FAM[f].color}">
        <div class="fam-head"><span class="fam-dot"></span>${FAM[f].name}</div>
        <div class="chips">${byFam.get(f).sort((a, b) => kindInfo(a)[1] - kindInfo(b)[1]).map((k) => `
          <button class="chip${S.hidden.has(k) ? ' off' : ''}" type="button" data-kind="${esc(k)}" aria-pressed="${!S.hidden.has(k)}">${esc(k.replace('_', ' '))}<b>${count.get(k)}</b></button>`).join('')}</div>
      </div>`).join('');

    const ranked = w.entities.filter((e) => e.reach > 0 && e.kind !== 'site').sort((a, b) => b.reach - a.reach || (a.id < b.id ? -1 : 1)).slice(0, 8);
    const sitesOut = w.entities.filter((e) => e.kind === 'site' && e.reach > 0).length;
    $('#gravity').innerHTML = ranked.map((e) => {
      const n = S.byId.get(e.id);
      return `<li><button type="button" data-go="${esc(e.id)}" data-preview="${esc(e.id)}" style="--c:${n.color}">
        <span class="g-name">${esc(e.id)}<small>${esc(e.kind.replace('_', ' '))}</small></span>
        <span class="g-val">${fmtInt(e.reach)} · ${pct(e.share)}</span>
        <span class="g-bar"><i style="width:${Math.max(3, e.share * 100)}%"></i></span></button></li>`;
    }).join('') || '<li class="none">Nothing takes anything with it.</li>';
    $('#gravityNote').textContent = sitesOut ? `${sitesOut} site${sitesOut > 1 ? 's' : ''} left out — everything in a datacentre goes with it.` : 'Structural reach. Declared replicas are ignored on purpose.';

    const a = w.audit;
    const groups = new Map();
    for (const f of a.findings || []) { if (!groups.has(f.check)) groups.set(f.check, []); groups.get(f.check).push(f); }
    const total = (a.findings || []).length + Object.keys(a.pervasive || {}).length;
    $('#healthCount').textContent = total ? `${total} to look at` : '';
    const sources = Object.entries(a.sources || {});
    $('#health').innerHTML = `
      <div class="health-sum">
        <div><b>${fmtInt(a.cross_confirmed || 0)}</b><span>confirmed by 2+ sources</span></div>
        <div><b>${fmtInt(sources.length)}</b><span>source${sources.length === 1 ? '' : 's'}: ${esc(sources.map(([k]) => k).join(', ') || 'none')}</span></div>
      </div>
      ${Object.entries(a.pervasive || {}).map(([k, v]) => `<div class="pervasive"><b>${esc(k)}</b> — ${fmtInt(v.hits)} of ${fmtInt(v.eligible)}: a gap in the data, not a list of defects.</div>`).join('')}
      ${[...groups.entries()].sort((x, y) => y[1].length - x[1].length).map(([check, items]) => `
        <div class="finding-group"><h4>${esc(check)} <span>${items.length}</span></h4>
          ${items.slice(0, 12).map((f) => `<button class="finding" type="button" data-go="${esc(f.id)}"><code>${esc(f.id)}</code><span>${esc(f.detail)}</span></button>`).join('')}
          ${items.length > 12 ? `<div class="none">… and ${items.length - 12} more</div>` : ''}
        </div>`).join('')}
      ${total ? '' : '<div class="all-clear">● Nothing to flag.</div>'}`;
  }
  $('#rail').addEventListener('click', (ev) => {
    const chip = ev.target.closest('[data-kind]');
    if (chip) {
      const k = chip.dataset.kind;
      if (S.hidden.has(k)) S.hidden.delete(k); else S.hidden.add(k);
      renderRail();
      return;
    }
    const go = ev.target.closest('[data-go]');
    if (go) {
      const n = S.byId.get(go.dataset.go);
      if (n) { S.preview = null; select(n); }
      if (S.W <= 900) toggleRail(false);
    }
  });
  $('#rail').addEventListener('mouseover', (ev) => {
    const p = ev.target.closest('[data-preview]');
    S.preview = p ? reachOf(S.byId.get(p.dataset.preview)) : null;
    if (!p) {
      const g = ev.target.closest('[data-go]');
      S.hover = g ? S.byId.get(g.dataset.go) || null : null;
    }
  });
  $('#rail').addEventListener('mouseleave', () => { S.preview = null; S.hover = null; });
  function toggleRail(open) {
    const rail = $('#rail');
    const v = open ?? !rail.classList.contains('open');
    rail.classList.toggle('open', v);
    $('#railToggle').setAttribute('aria-expanded', String(v));
  }
  $('#railToggle').addEventListener('click', () => toggleRail());

  function renderStats() {
    const w = S.world;
    const findings = (w.audit.findings || []).length;
    const reached = w.entities.reduce((m, e) => Math.max(m, e.share || 0), 0);
    $('#stats').innerHTML = `
      <span class="stat"><b>${fmtInt(w.entities.length)}</b><span>entities</span></span>
      <span class="stat"><b>${fmtInt(w.relations.length)}</b><span>relations</span></span>
      <span class="stat"><b>${fmtInt(S.rings.length)}</b><span>layers</span></span>
      <span class="stat${findings ? ' warn' : ''}"><b>${fmtInt(findings)}</b><span>findings</span></span>
      <span class="stat"><b>${pct(reached)}</b><span>largest single reach</span></span>`;
    const badge = $('#modeBadge');
    if (w.mode === 'snapshot') {
      badge.textContent = `Snapshot${w.generated_at ? ' · ' + w.generated_at.slice(0, 10) : ''}`;
      badge.classList.add('snapshot');
      badge.title = 'Exported with orrery map --export: every answer is precomputed';
    } else {
      badge.textContent = 'Live engine';
      badge.title = `orrery ${w.version || ''} is answering every click`;
    }
  }

  // --------------------------------------------------------------- palette --
  const palette = $('#palette'), pInput = $('#paletteInput'), pList = $('#paletteList');
  let pItems = [], pIndex = 0;
  function openPalette() {
    palette.hidden = false;
    pInput.value = '';
    filterPalette();
    pInput.focus();
  }
  function closePalette() { palette.hidden = true; canvas.focus({ preventScroll: true }); }
  function filterPalette() {
    const q = pInput.value.trim().toLowerCase();
    const scored = [];
    for (const n of S.nodes) {
      const id = n.id.toLowerCase(), name = (n.e.name || '').toLowerCase(), kind = n.e.kind;
      let s = -1;
      if (!q) s = n.e.reach || 0;
      else if (id === q) s = 1000;
      else if (id.startsWith(q)) s = 500 - id.length;
      else if (id.includes(q)) s = 300 - id.indexOf(q);
      else if (name.includes(q)) s = 200 - name.indexOf(q);
      else if (kind.includes(q)) s = 100;
      if (s >= 0) scored.push([s, n]);
    }
    scored.sort((a, b) => b[0] - a[0] || (a[1].id < b[1].id ? -1 : 1));
    pItems = scored.slice(0, 40).map((x) => x[1]);
    pIndex = 0;
    const hl = (s) => {
      const i = q ? s.toLowerCase().indexOf(q) : -1;
      return i < 0 ? esc(s) : esc(s.slice(0, i)) + '<mark>' + esc(s.slice(i, i + q.length)) + '</mark>' + esc(s.slice(i + q.length));
    };
    pList.innerHTML = pItems.length ? pItems.map((n, i) => `
      <li role="option" id="p-${i}" data-i="${i}" aria-selected="${i === 0}" style="--c:${n.color}">
        <span class="p-dot"></span>
        <span class="p-main"><b>${hl(n.id)}</b><span>${hl(n.e.name || '')}${n.e.reach ? ` · ${fmtInt(n.e.reach)} go with it` : ''}</span></span>
        <span class="p-kind">${esc(n.e.kind.replace('_', ' '))}</span></li>`).join('')
      : '<div class="palette-empty">No entity matches.</div>';
  }
  function movePalette(d) {
    if (!pItems.length) return;
    pIndex = (pIndex + d + pItems.length) % pItems.length;
    for (const li of pList.children) li.setAttribute('aria-selected', String(+li.dataset.i === pIndex));
    pList.children[pIndex]?.scrollIntoView({ block: 'nearest' });
  }
  function pickPalette(i) {
    const n = pItems[i];
    if (!n) return;
    closePalette();
    if (S.hidden.has(n.e.kind)) { S.hidden.delete(n.e.kind); renderRail(); }
    select(n);
  }
  pInput.addEventListener('input', filterPalette);
  pInput.addEventListener('keydown', (ev) => {
    if (ev.key === 'ArrowDown') { ev.preventDefault(); movePalette(1); }
    else if (ev.key === 'ArrowUp') { ev.preventDefault(); movePalette(-1); }
    else if (ev.key === 'Enter') { ev.preventDefault(); pickPalette(pIndex); }
    else if (ev.key === 'Escape') { ev.preventDefault(); closePalette(); }
  });
  pList.addEventListener('click', (ev) => { const li = ev.target.closest('li[data-i]'); if (li) pickPalette(+li.dataset.i); });
  palette.addEventListener('mousedown', (ev) => { if (ev.target === palette) closePalette(); });
  $('#searchBtn').addEventListener('click', openPalette);

  // ------------------------------------------------------------------ input --
  function hit(sx, sy) {
    const p = toWorld(sx, sy);
    let best = null, bd = Infinity;
    const slack = 7 / S.cam.k;
    for (const n of S.nodes) {
      if (S.hidden.has(n.e.kind)) continue;
      const d = Math.hypot(n.x - p.x, n.y - p.y);
      if (d < n.size + slack && d < bd) { bd = d; best = n; }
    }
    return best;
  }
  const pointers = new Map();
  let drag = null, pinch = null;
  canvas.addEventListener('pointerdown', (ev) => {
    canvas.setPointerCapture(ev.pointerId);
    pointers.set(ev.pointerId, { x: ev.offsetX, y: ev.offsetY });
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      pinch = { d: Math.hypot(a.x - b.x, a.y - b.y), k: S.cam.k, cx: (a.x + b.x) / 2, cy: (a.y + b.y) / 2 };
      drag = null;
    } else {
      drag = { x: ev.offsetX, y: ev.offsetY, cx: S.cam.x, cy: S.cam.y, moved: false };
    }
    S.camTo = null;
  });
  canvas.addEventListener('pointermove', (ev) => {
    if (pointers.has(ev.pointerId)) pointers.set(ev.pointerId, { x: ev.offsetX, y: ev.offsetY });
    if (pinch && pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      zoomAt(pinch.cx, pinch.cy, (pinch.k * d) / pinch.d / S.cam.k);
      return;
    }
    if (drag) {
      const dx = ev.offsetX - drag.x, dy = ev.offsetY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
      if (drag.moved) {
        S.cam.x = drag.cx + dx; S.cam.y = drag.cy + dy;
        canvas.classList.add('dragging');
        hideTooltip();
        return;
      }
    }
    const n = hit(ev.offsetX, ev.offsetY);
    S.hover = n;
    canvas.classList.toggle('pointing', !!n);
    if (n && ev.pointerType === 'mouse') showTooltip(n, ev.offsetX, ev.offsetY); else hideTooltip();
  });
  const endPointer = (ev) => {
    pointers.delete(ev.pointerId);
    if (pointers.size < 2) pinch = null;
    if (drag && !drag.moved && ev.type === 'pointerup') {
      const n = hit(ev.offsetX, ev.offsetY);
      if (n) select(n, { fly: false });
      else if (S.selected && !S.result) select(null);
    }
    drag = null;
    canvas.classList.remove('dragging');
  };
  canvas.addEventListener('pointerup', endPointer);
  canvas.addEventListener('pointercancel', endPointer);
  canvas.addEventListener('pointerleave', () => { S.hover = null; hideTooltip(); });
  canvas.addEventListener('dblclick', (ev) => {
    const n = hit(ev.offsetX, ev.offsetY);
    if (n) simulate(n.id, 'down'); else S.camTo = fitCam();
  });
  canvas.addEventListener('wheel', (ev) => {
    ev.preventDefault();
    S.camTo = null;
    const f = Math.exp(-ev.deltaY * (ev.ctrlKey ? 0.012 : 0.0016));
    zoomAt(ev.offsetX, ev.offsetY, f);
  }, { passive: false });
  function zoomAt(sx, sy, f) {
    const k = Math.max(0.05, Math.min(8, S.cam.k * f));
    const w = toWorld(sx, sy);
    S.cam.k = k;
    S.cam.x = sx - S.W / 2 - w.x * k;
    S.cam.y = sy - S.H / 2 - w.y * k;
  }
  $('.zoom').addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-zoom]');
    if (!b) return;
    const z = b.dataset.zoom;
    if (z === 'fit') { S.camTo = fitCam(); return; }
    const f = z === 'in' ? 1.35 : 1 / 1.35;
    const k = Math.max(0.05, Math.min(8, S.cam.k * f));
    S.camTo = { k, x: S.cam.x * (k / S.cam.k), y: S.cam.y * (k / S.cam.k) };
  });

  function showTooltip(n, x, y) {
    const e = n.e;
    const R = S.result;
    let row = `<div class="tt-row"><b>${fmtInt(e.reach || 0)}</b> go with it · ${n.out.length} stands on · ${n.in.length} need it</div>`;
    if (R && R.mode === 'sim' && R.status.has(n.id)) {
      const st = R.status.get(n.id);
      row = `<div class="tt-row" style="--sc:${STATUS_COLOR[st.status]}"><b>${esc(st.status)}</b> — ${esc(st.why || 'passthrough')}</div>`;
    } else if (R && R.mode === 'blast' && R.wave.has(n.id) && n.id !== R.trigger) {
      row = `<div class="tt-row" style="--sc:${STATUS_COLOR.range}"><b>hop ${R.wave.get(n.id)}</b> from ${esc(R.trigger)}</div>`;
    }
    tooltip.innerHTML = `<div class="tt-kind" style="--c:${n.color}">${esc(e.kind.replace('_', ' '))}</div>
      <div class="tt-name">${esc(e.name || e.id)}</div><div class="tt-id">${esc(e.id)}</div>${row}`;
    tooltip.hidden = false;
    const w = tooltip.offsetWidth, h = tooltip.offsetHeight;
    tooltip.style.left = `${Math.min(x, S.W - w - 30)}px`;
    tooltip.style.top = `${Math.min(y, S.H - h - 30)}px`;
  }
  function hideTooltip() { tooltip.hidden = true; }

  document.addEventListener('keydown', (ev) => {
    if (!palette.hidden) return;
    const typing = ev.target.closest && ev.target.closest('input, textarea');
    if ((ev.key === 'k' && (ev.metaKey || ev.ctrlKey)) || (ev.key === '/' && !typing)) {
      ev.preventDefault(); openPalette(); return;
    }
    if (typing || ev.metaKey || ev.ctrlKey || ev.altKey) return;
    const key = ev.key.toLowerCase();
    if (ev.key === 'Escape') {
      if (S.result) clearResult(); else if (S.selected) select(null);
      else if ($('#rail').classList.contains('open')) toggleRail(false);
    } else if (key === 'f') S.camTo = fitCam();
    else if (S.selected && key === 's') simulate(S.selected.id, 'down');
    else if (S.selected && key === 'd') simulate(S.selected.id, 'degraded');
    else if (S.selected && key === 'b') blast(S.selected.id);
    else if (key === '+' || key === '=') zoomAt(S.W / 2, S.H / 2, 1.25);
    else if (key === '-') zoomAt(S.W / 2, S.H / 2, 0.8);
  });

  let toastTimer = 0;
  function toast(msg) {
    let el = $('.toast');
    if (!el) {
      el = document.createElement('div');
      el.className = 'toast callout';
      el.style.cssText = 'position:absolute;left:50%;bottom:22px;transform:translateX(-50%);z-index:12;--cc:var(--down);background:var(--panel-solid);max-width:min(520px,90vw)';
      el.setAttribute('role', 'alert');
      app.appendChild(el);
    }
    el.innerHTML = `<b>Could not compute that</b>${esc(msg)}`;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 5200);
  }

  // ------------------------------------------------------------------- boot --
  async function boot() {
    resize();
    addEventListener('resize', () => { const fit = !S.camTo; resize(); if (fit) S.camTo = fitCam(); });
    let world;
    try {
      world = await Source.world();
    } catch (err) {
      const sp = $('#splash');
      sp.classList.add('error');
      $('#splashText').innerHTML = `No world to draw. ${esc(err.message)}<br><br>Run <code>orrery ingest &lt;world.yaml&gt;</code>, then <code>orrery map</code>.`;
      return;
    }
    S.world = world;
    if (!world.entities.length) {
      $('#splash').classList.add('error');
      $('#splashText').innerHTML = 'This world is empty. Ingest something first: <code>orrery ingest &lt;world.yaml&gt;</code>.';
      return;
    }
    const L = layout(world);
    Object.assign(S, L);
    S.cam = fitCam();
    S.cam.k *= 0.9;
    S.camTo = fitCam();
    renderStats();
    renderRail();
    document.title = `orrery — ${fmtInt(world.entities.length)} entities`;
    S.t0 = performance.now();
    app.classList.remove('booting');
    requestAnimationFrame(frame);
    // deep link: #entity-id selects it, #simulate=entity-id runs it
    const h = decodeURIComponent(location.hash.slice(1));
    if (h) {
      const [cmd, arg] = h.includes('=') ? h.split('=') : ['select', h];
      const n = S.byId.get(arg);
      if (n) setTimeout(() => (cmd === 'simulate' ? simulate(n.id, 'down') : cmd === 'blast' ? blast(n.id) : select(n)), 1500);
    }
  }
  boot();
})();
