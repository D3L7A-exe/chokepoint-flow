/* Chokepoint web UI. No dependencies. All server text is inserted with textContent. */
(() => {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const MAP_W = 900;
  const MAP_H = 560;
  const REFRESH_MS = 5000;

  const state = { window: 300, collapse: true, paused: false, tableView: false, timer: null };
  const $ = (sel) => document.querySelector(sel);

  /* ---------- helpers ---------- */

  function svgEl(tag, attrs, parent) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
    if (parent) parent.appendChild(node);
    return node;
  }

  function htmlEl(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function setHidden(node, hidden) {
    node.toggleAttribute("hidden", hidden);
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function fmtBytes(n) {
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    let v = n;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return (i === 0 ? String(Math.round(v)) : v.toFixed(v >= 100 ? 0 : 1)) + " " + units[i];
  }

  function fmtRate(mbps) {
    if (mbps >= 100) return mbps.toFixed(0) + " Mbit/s";
    if (mbps >= 1) return mbps.toFixed(1) + " Mbit/s";
    if (mbps >= 0.001) return (mbps * 1000).toFixed(mbps >= 0.1 ? 0 : 1) + " kbit/s";
    return "0 bit/s";
  }

  function fmtClock(ts, withSeconds) {
    const d = new Date(ts * 1000);
    const pad = (x) => String(x).padStart(2, "0");
    return pad(d.getHours()) + ":" + pad(d.getMinutes()) + (withSeconds ? ":" + pad(d.getSeconds()) : "");
  }

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function hexToRgb(hex) {
    const h = hex.replace("#", "");
    return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  }

  /* Sequential single-hue colour for a 0..1 share, interpolated between the theme's ramp steps. */
  function heat(share) {
    const steps = [0, 1, 2, 3, 4].map((i) => hexToRgb(cssVar("--heat-" + i)));
    const x = Math.min(Math.max(share, 0), 1) * (steps.length - 1);
    const i = Math.min(Math.floor(x), steps.length - 2);
    const f = x - i;
    const c = steps[i].map((v, k) => Math.round(v + (steps[i + 1][k] - v) * f));
    return "rgb(" + c.join(",") + ")";
  }

  async function getJson(url) {
    const res = await fetch(url, { cache: "no-store", credentials: "same-origin" });
    if (!res.ok) throw new Error(url + " -> " + res.status);
    return res.json();
  }

  /* ---------- tooltip (one shared element; content built with textContent) ---------- */

  const tip = $("#tip");

  function showTip(clientX, clientY, build) {
    clear(tip);
    build(tip);
    tip.hidden = false;
    const r = tip.getBoundingClientRect();
    let x = clientX + 14;
    let y = clientY + 14;
    if (x + r.width > window.innerWidth - 8) x = clientX - r.width - 14;
    if (y + r.height > window.innerHeight - 8) y = clientY - r.height - 14;
    tip.style.left = Math.max(8, x) + "px";
    tip.style.top = Math.max(8, y) + "px";
  }

  function hideTip() { tip.hidden = true; }

  function tipLines(container, value, label, extra) {
    container.appendChild(htmlEl("div", "tv", value));
    container.appendChild(htmlEl("div", "tl", label));
    if (extra) container.appendChild(htmlEl("div", "tl", extra));
  }

  /* ---------- network map: small force-directed layout ---------- */

  const sim = { nodes: new Map(), links: new Map(), alpha: 0, raf: 0, maxNode: 1, linkLayer: null, nodeLayer: null };

  /* Merge a fresh API graph into the persistent simulation state.
     Returns true when hosts or links appeared or disappeared (then the layout should move). */
  function mergeGraph(graph) {
    let changed = false;
    const seen = new Set();
    for (const n of graph.nodes) {
      seen.add(n.id);
      let node = sim.nodes.get(n.id);
      if (!node) {
        const angle = Math.random() * Math.PI * 2;
        const radius = 150 + Math.random() * 220;
        node = { id: n.id, x: MAP_W / 2 + Math.cos(angle) * radius, y: MAP_H / 2 + Math.sin(angle) * radius, vx: 0, vy: 0, fixed: false, dragging: false, g: null };
        sim.nodes.set(n.id, node);
        changed = true;
      }
      Object.assign(node, { label: n.label, kind: n.kind, bytes: n.bytes, peers: 0 });
    }
    for (const [id, node] of [...sim.nodes]) {
      if (!seen.has(id)) { if (node.g) node.g.remove(); sim.nodes.delete(id); changed = true; }
    }

    const maxLink = Math.max(1, ...graph.links.map((l) => l.bytes));
    const seenLinks = new Set();
    for (const l of graph.links) {
      const key = l.source + "|" + l.target;
      seenLinks.add(key);
      let link = sim.links.get(key);
      if (!link) { link = { key, g: null }; sim.links.set(key, link); changed = true; }
      const a = sim.nodes.get(l.source);
      const b = sim.nodes.get(l.target);
      a.peers++;
      b.peers++;
      Object.assign(link, { a, b, tx: l.tx, rx: l.rx, bytes: l.bytes, mbps: l.mbps, share: l.bytes / maxLink });
    }
    for (const [key, link] of [...sim.links]) {
      if (!seenLinks.has(key)) { if (link.g) link.g.remove(); sim.links.delete(key); changed = true; }
    }
    sim.maxNode = Math.max(1, ...graph.nodes.map((n) => n.bytes));
    return changed;
  }

  function stepSim() {
    const nodes = [...sim.nodes.values()];
    const alpha = sim.alpha;
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i];
        const b = nodes[j];
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 1; }
        const d = Math.sqrt(d2);
        const f = Math.min(9000 / d, 40) * alpha * 0.12;
        a.vx -= (dx / d) * f; a.vy -= (dy / d) * f;
        b.vx += (dx / d) * f; b.vy += (dy / d) * f;
      }
    }
    for (const l of sim.links.values()) {
      const dx = l.b.x - l.a.x;
      const dy = l.b.y - l.a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const f = (d - 170) * 0.012 * (0.4 + l.share) * Math.max(alpha, 0.2);
      l.a.vx += (dx / d) * f; l.a.vy += (dy / d) * f;
      l.b.vx -= (dx / d) * f; l.b.vy -= (dy / d) * f;
    }
    for (const n of nodes) {
      n.vx += (MAP_W / 2 - n.x) * 0.0015;
      n.vy += (MAP_H / 2 - n.y) * 0.0025;
      if (n.fixed) { n.vx = 0; n.vy = 0; continue; }
      n.vx *= 0.8; n.vy *= 0.8;
      n.x = Math.min(Math.max(n.x + n.vx, 40), MAP_W - 40);
      n.y = Math.min(Math.max(n.y + n.vy, 30), MAP_H - 30);
    }
    sim.alpha *= 0.97;
  }

  function paintPositions() {
    for (const l of sim.links.values()) {
      if (!l.g) continue;
      for (const line of l.lines) {
        line.setAttribute("x1", l.a.x.toFixed(1)); line.setAttribute("y1", l.a.y.toFixed(1));
        line.setAttribute("x2", l.b.x.toFixed(1)); line.setAttribute("y2", l.b.y.toFixed(1));
      }
    }
    for (const n of sim.nodes.values()) {
      if (!n.g) continue;
      n.g.setAttribute("transform", "translate(" + n.x.toFixed(1) + " " + n.y.toFixed(1) + ")");
      const right = n.x < MAP_W / 2 + 120;
      n.text.setAttribute("x", right ? n.r + 6 : -(n.r + 6));
      n.text.setAttribute("text-anchor", right ? "start" : "end");
    }
  }

  function animate() {
    sim.raf = 0;
    stepSim();
    paintPositions();
    if (sim.alpha > 0.02) sim.raf = requestAnimationFrame(animate);
  }

  function kick(alpha) {
    sim.alpha = Math.max(sim.alpha, alpha);
    if (!sim.raf) sim.raf = requestAnimationFrame(animate);
  }

  function highlight(node) {
    for (const l of sim.links.values()) {
      const on = !node || l.a === node || l.b === node;
      if (l.g) l.g.classList.toggle("dim", !on);
    }
  }

  function nodeTooltip(evt, n) {
    showTip(evt.clientX, evt.clientY, (t) => {
      tipLines(t, fmtBytes(n.bytes), n.label, n.label !== n.id && n.kind === "host" ? n.id : n.peers + " connected " + (n.peers === 1 ? "host" : "hosts"));
    });
  }

  function linkTooltip(evt, l) {
    showTip(evt.clientX, evt.clientY, (t) => {
      tipLines(t, fmtBytes(l.bytes) + " · " + fmtRate(l.mbps), l.a.label + " ⇄ " + l.b.label,
        l.a.label + " → " + l.b.label + ": " + fmtBytes(l.tx) + "  |  reverse: " + fmtBytes(l.rx));
    });
  }

  function svgPoint(svg, evt) {
    const pt = svg.createSVGPoint();
    pt.x = evt.clientX; pt.y = evt.clientY;
    return pt.matrixTransform(svg.getScreenCTM().inverse());
  }

  function createLinkElements(l) {
    l.g = svgEl("g", {}, sim.linkLayer);
    const line = svgEl("line", { class: "link-line" }, l.g);
    const hit = svgEl("line", { class: "link-hit" }, l.g);
    l.lines = [line, hit];
    l.line = line;
    hit.addEventListener("pointermove", (e) => linkTooltip(e, l));
    hit.addEventListener("pointerleave", hideTip);
  }

  function createNodeElements(n, svg) {
    n.g = svgEl("g", { tabindex: "0", role: "img" }, sim.nodeLayer);
    n.circle = svgEl("circle", {}, n.g);
    n.text = svgEl("text", { y: 4 }, n.g);

    n.g.addEventListener("pointerenter", (e) => { highlight(n); nodeTooltip(e, n); });
    n.g.addEventListener("pointerleave", () => { if (!n.dragging) { highlight(null); hideTip(); } });
    n.g.addEventListener("focus", () => {
      highlight(n);
      const r = n.g.getBoundingClientRect();
      nodeTooltip({ clientX: r.right, clientY: r.top }, n);
    });
    n.g.addEventListener("blur", () => { highlight(null); hideTip(); });
    n.g.addEventListener("pointerdown", (e) => {
      n.dragging = true; n.fixed = true;
      n.g.setPointerCapture(e.pointerId);
      hideTip();
    });
    n.g.addEventListener("pointermove", (e) => {
      if (!n.dragging) { nodeTooltip(e, n); return; }
      const p = svgPoint(svg, e);
      n.x = Math.min(Math.max(p.x, 30), MAP_W - 30);
      n.y = Math.min(Math.max(p.y, 20), MAP_H - 20);
      kick(0.3);
      paintPositions();
    });
    const release = () => { n.dragging = false; n.fixed = false; };
    n.g.addEventListener("pointerup", release);
    n.g.addEventListener("pointercancel", release);
  }

  function renderMap(graph) {
    const svg = $("#map");
    const empty = $("#map-empty");
    const table = $("#map-table");

    if (graph.nodes.length === 0) {
      sim.nodes.clear();
      sim.links.clear();
      clear(svg);
      sim.linkLayer = sim.nodeLayer = null;
      setHidden(svg, true); setHidden(table, true); setHidden(empty, false);
      empty.textContent = "No traffic in this window. Check that the collector is running and an exporter is sending NetFlow v5 to UDP port 2055.";
      return;
    }
    setHidden(empty, true);
    setHidden(svg, state.tableView);
    setHidden(table, !state.tableView);

    if (!sim.linkLayer || !sim.linkLayer.isConnected) {
      clear(svg);
      sim.linkLayer = svgEl("g", {}, svg);
      sim.nodeLayer = svgEl("g", {}, svg);
      for (const n of sim.nodes.values()) n.g = null;
      for (const l of sim.links.values()) l.g = null;
    }

    const changed = mergeGraph(graph);

    for (const l of sim.links.values()) {
      if (!l.g) createLinkElements(l);
      l.line.setAttribute("stroke", heat(l.share));
      l.line.setAttribute("stroke-width", (1.5 + 7 * Math.sqrt(l.share)).toFixed(2));
    }
    for (const n of sim.nodes.values()) {
      if (!n.g) createNodeElements(n, svg);
      n.r = 7 + 9 * Math.sqrt(n.bytes / sim.maxNode);
      n.g.setAttribute("class", "node " + n.kind);
      n.g.setAttribute("aria-label", n.label + ", " + fmtBytes(n.bytes));
      n.circle.setAttribute("r", n.r.toFixed(1));
      n.text.textContent = n.label;
    }
    renderMapTable();
    paintPositions();
    if (changed) kick(0.6);
  }

  function renderMapTable() {
    const table = $("#map-table");
    clear(table);
    const thead = htmlEl("thead");
    const hr = htmlEl("tr");
    ["Host A", "Host B", "A → B", "B → A", "Total", "Rate"].forEach((h, i) => {
      hr.appendChild(htmlEl("th", i >= 2 ? "num" : "", h));
    });
    thead.appendChild(hr);
    table.appendChild(thead);
    const tbody = htmlEl("tbody");
    const rows = [...sim.links.values()].sort((x, y) => y.bytes - x.bytes).slice(0, 100);
    for (const l of rows) {
      const tr = htmlEl("tr");
      tr.appendChild(htmlEl("td", "", l.a.label));
      tr.appendChild(htmlEl("td", "", l.b.label));
      tr.appendChild(htmlEl("td", "num", fmtBytes(l.tx)));
      tr.appendChild(htmlEl("td", "num", fmtBytes(l.rx)));
      tr.appendChild(htmlEl("td", "num", fmtBytes(l.bytes)));
      tr.appendChild(htmlEl("td", "num", fmtRate(l.mbps)));
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
  }

  /* ---------- ranked bars (top talkers) ---------- */

  function renderTalkers(top) {
    const box = $("#talkers");
    clear(box);
    if (top.talkers.length === 0) {
      box.appendChild(htmlEl("p", "hint", "No traffic yet."));
      return;
    }
    const max = Math.max(1, top.talkers[0].bytes);
    for (const t of top.talkers) {
      const row = htmlEl("div", "rank");
      row.appendChild(htmlEl("div", "name", t.label));
      const track = htmlEl("div", "track");
      const fill = htmlEl("div", "fill");
      fill.style.width = Math.max(2, (t.bytes / max) * 100).toFixed(1) + "%";
      track.appendChild(fill);
      row.appendChild(track);
      row.appendChild(htmlEl("div", "val", fmtBytes(t.bytes)));
      row.title = t.ip;
      box.appendChild(row);
    }
  }

  /* ---------- link status (bottlenecks): icon + label, never colour alone ---------- */

  function statusIcon(level) {
    const svg = svgEl("svg", { width: 16, height: 16, viewBox: "0 0 16 16", "aria-hidden": "true" });
    const color = "var(--" + (level === "ok" ? "good" : level) + ")";
    if (level === "ok") {
      svgEl("circle", { cx: 8, cy: 8, r: 7, fill: color }, svg);
      svgEl("path", { d: "M4.5 8.2l2.4 2.4 4.6-4.8", fill: "none", stroke: "#fff", "stroke-width": 1.8, "stroke-linecap": "round", "stroke-linejoin": "round" }, svg);
    } else if (level === "warning") {
      svgEl("path", { d: "M8 1.5L15 14H1z", fill: color, stroke: color, "stroke-linejoin": "round" }, svg);
      svgEl("path", { d: "M8 6v4M8 11.6v.4", stroke: "#0b0b0b", "stroke-width": 1.8, "stroke-linecap": "round" }, svg);
    } else {
      svgEl("path", { d: "M8 1l7 7-7 7-7-7z", fill: color, stroke: color, "stroke-linejoin": "round" }, svg);
      svgEl("path", { d: "M8 4.8v4M8 10.8v.4", stroke: "#fff", "stroke-width": 1.8, "stroke-linecap": "round" }, svg);
    }
    return svg;
  }

  function renderLinks(data) {
    const box = $("#links");
    clear(box);
    if (!data.configured) {
      const p = htmlEl("p", "hint");
      p.appendChild(document.createTextNode("No link capacities configured. Copy "));
      p.appendChild(htmlEl("code", "", "links.example.toml"));
      p.appendChild(document.createTextNode(" to "));
      p.appendChild(htmlEl("code", "", "links.toml"));
      p.appendChild(document.createTextNode(" and restart to see utilization and bottlenecks per link."));
      box.appendChild(p);
      return;
    }
    const labels = { ok: "OK", warning: "Warning", critical: "Critical" };
    for (const l of data.links) {
      const row = htmlEl("div", "status-row");
      const top = htmlEl("div", "status-top");
      top.appendChild(statusIcon(l.level));
      top.appendChild(htmlEl("span", "name", l.name));
      top.appendChild(htmlEl("span", "label", labels[l.level] + " · " + (l.utilization * 100).toFixed(1) + "%"));
      row.appendChild(top);
      row.appendChild(htmlEl("div", "status-sub", fmtRate(l.mbps) + " of " + l.capacity_mbps + " Mbit/s"));
      const meter = htmlEl("div", "meter");
      const fill = htmlEl("div", "lvl-" + l.level);
      fill.style.width = Math.min(100, Math.max(1, l.utilization * 100)).toFixed(1) + "%";
      meter.appendChild(fill);
      row.appendChild(meter);
      box.appendChild(row);
    }
  }

  /* ---------- KPIs and flow table ---------- */

  function renderKpis(summary, graph, links) {
    $("#kpi-rate").textContent = fmtRate(summary.mbps);
    $("#kpi-bytes").textContent = fmtBytes(summary.bytes) + " total";
    $("#kpi-flows").textContent = summary.flows.toLocaleString();
    $("#kpi-hosts").textContent = graph.nodes.filter((n) => n.kind === "host").length.toString();
    if (links.configured && links.links.length) {
      const worst = links.links[0];
      $("#kpi-link").textContent = (worst.utilization * 100).toFixed(0) + "%";
      $("#kpi-link-sub").textContent = worst.name;
    } else {
      $("#kpi-link").textContent = "–";
      $("#kpi-link-sub").textContent = "no link config";
    }
  }

  function renderFlows(top) {
    const body = $("#flows tbody");
    clear(body);
    for (const f of top.flows) {
      const tr = htmlEl("tr");
      tr.appendChild(htmlEl("td", "", f.src_label));
      tr.appendChild(htmlEl("td", "", f.dst_label));
      tr.appendChild(htmlEl("td", "num", String(f.dport)));
      tr.appendChild(htmlEl("td", "num", fmtBytes(f.bytes)));
      body.appendChild(tr);
    }
    if (top.flows.length === 0) {
      const tr = htmlEl("tr");
      const td = htmlEl("td", "", "No flows in this window.");
      td.colSpan = 4;
      tr.appendChild(td);
      body.appendChild(tr);
    }
  }

  /* ---------- timeline: one series, crosshair + tooltip ---------- */

  function niceMax(v) {
    if (v <= 0) return 1;
    const exp = Math.pow(10, Math.floor(Math.log10(v)));
    const f = v / exp;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * exp;
  }

  function tickLabel(v) {
    if (v === 0) return "0";
    if (v >= 10) return v.toFixed(0);
    return v >= 1 ? v.toFixed(1) : v.toPrecision(2);
  }

  function renderTimeline(data) {
    const svg = $("#timeline");
    clear(svg);
    const W = 900, H = 220, L = 80, R = 12, T = 10, B = 26;
    const pts = data.points;
    const max = niceMax(Math.max(0, ...pts.map((p) => p.mbps)));
    const x = (i) => L + (pts.length < 2 ? 0 : (i / (pts.length - 1)) * (W - L - R));
    const y = (v) => T + (1 - v / max) * (H - T - B);

    for (const frac of [0, 0.5, 1]) {
      const v = max * frac;
      svgEl("line", { class: frac === 0 ? "base" : "grid", x1: L, x2: W - R, y1: y(v), y2: y(v) }, svg);
      const t = svgEl("text", { class: "tick", x: L - 8, y: y(v) + 4, "text-anchor": "end" }, svg);
      t.textContent = tickLabel(v) + (frac === 1 ? " Mbit/s" : "");
    }
    for (let k = 0; k < 5; k++) {
      const i = Math.round((k / 4) * (pts.length - 1));
      const t = svgEl("text", { class: "tick", x: x(i), y: H - 6, "text-anchor": k === 0 ? "start" : k === 4 ? "end" : "middle" }, svg);
      t.textContent = fmtClock(pts[i].t, data.window_seconds <= 300);
    }
    if (pts.length < 2) return;

    const line = pts.map((p, i) => (i ? "L" : "M") + x(i).toFixed(1) + " " + y(p.mbps).toFixed(1)).join(" ");
    svgEl("path", { class: "area", d: line + " L" + x(pts.length - 1).toFixed(1) + " " + y(0) + " L" + x(0).toFixed(1) + " " + y(0) + " Z" }, svg);
    svgEl("path", { class: "line", d: line }, svg);

    const cross = svgEl("line", { class: "cross", y1: T, y2: H - B, visibility: "hidden" }, svg);
    const dot = svgEl("circle", { class: "dot", r: 4, visibility: "hidden" }, svg);
    const hit = svgEl("rect", { x: L, y: T, width: W - L - R, height: H - T - B, fill: "transparent", tabindex: "0", "aria-label": "Traffic timeline, use left and right arrow keys" }, svg);

    let idx = pts.length - 1;
    const show = (i, clientX, clientY) => {
      idx = Math.min(Math.max(i, 0), pts.length - 1);
      const p = pts[idx];
      cross.setAttribute("x1", x(idx)); cross.setAttribute("x2", x(idx)); cross.setAttribute("visibility", "visible");
      dot.setAttribute("cx", x(idx)); dot.setAttribute("cy", y(p.mbps)); dot.setAttribute("visibility", "visible");
      showTip(clientX, clientY, (t) => {
        const row = htmlEl("div", "row");
        row.appendChild(htmlEl("span", "key"));
        row.appendChild(htmlEl("span", "tv", fmtRate(p.mbps)));
        t.appendChild(row);
        t.appendChild(htmlEl("div", "tl", fmtClock(p.t, true) + " · " + fmtBytes(p.bytes) + " in bucket"));
      });
    };
    const leave = () => {
      cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); hideTip();
    };
    hit.addEventListener("pointermove", (e) => {
      const p = svgPoint(svg, e);
      show(Math.round(((p.x - L) / (W - L - R)) * (pts.length - 1)), e.clientX, e.clientY);
    });
    hit.addEventListener("pointerleave", leave);
    hit.addEventListener("blur", leave);
    hit.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      const r = hit.getBoundingClientRect();
      show(idx + (e.key === "ArrowRight" ? 1 : -1), r.left + ((x(idx) - L) / (W - L - R)) * r.width, r.top);
    });
  }

  /* ---------- refresh loop ---------- */

  function setStatus(text, isError) {
    const el = $("#status");
    el.textContent = text;
    el.classList.toggle("error", Boolean(isError));
  }

  async function load() {
    const q = "window=" + state.window;
    document.body.classList.add("loading");
    try {
      const [summary, graph, top, links, timeline] = await Promise.all([
        getJson("/api/summary?" + q),
        getJson("/api/graph?" + q + "&collapse=" + (state.collapse ? 1 : 0)),
        getJson("/api/top?" + q + "&limit=8"),
        getJson("/api/links?" + q),
        getJson("/api/timeline?" + q + "&buckets=60"),
      ]);
      renderKpis(summary, graph, links);
      renderMap(graph);
      renderTalkers(top);
      renderLinks(links);
      renderFlows(top);
      renderTimeline(timeline);
      setStatus("Updated " + fmtClock(Date.now() / 1000, true), false);
    } catch (err) {
      setStatus("Connection problem, retrying…", true);
      console.error(err);
    } finally {
      document.body.classList.remove("loading");
    }
  }

  function schedule() {
    clearInterval(state.timer);
    if (!state.paused) state.timer = setInterval(load, REFRESH_MS);
  }

  /* ---------- controls ---------- */

  $("#windows").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-window]");
    if (!btn) return;
    state.window = Number(btn.dataset.window);
    for (const b of document.querySelectorAll("#windows button")) b.setAttribute("aria-pressed", String(b === btn));
    load();
  });

  $("#collapse").addEventListener("change", (e) => { state.collapse = e.target.checked; load(); });

  $("#pause").addEventListener("click", (e) => {
    state.paused = !state.paused;
    e.target.setAttribute("aria-pressed", String(state.paused));
    e.target.textContent = state.paused ? "Resume" : "Pause";
    schedule();
    if (!state.paused) load();
  });

  $("#view-toggle").addEventListener("click", (e) => {
    state.tableView = !state.tableView;
    e.target.setAttribute("aria-pressed", String(state.tableView));
    e.target.textContent = state.tableView ? "Map view" : "Table view";
    if (sim.nodes.size) {
      setHidden($("#map"), state.tableView);
      setHidden($("#map-table"), !state.tableView);
    }
  });

  load();
  schedule();
})();
