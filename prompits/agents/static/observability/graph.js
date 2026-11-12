/** Graph identity and layout are scoped to authorized snapshot contents. */
export const agentKey = (source, id) => source + "::" + id;
export const plazaKey = (source) => "@plaza:" + source;
const palette = [
  "#5eb6ff",
  "#36d6d9",
  "#ff80be",
  "#b99aff",
  "#ffd166",
  "#a8db62",
  "#ff9b66",
  "#e0e7ee",
];
export function typeColor(type, registeredTypes = []) {
  // Allocate distinct colors from the complete authorized registry, independent
  // of name, health, type or Plaza filters. No product-specific type names.
  const types = [...new Set(registeredTypes)].sort();
  const index = types.indexOf(type);
  if (index >= 0)
    return palette[index] || `hsl(${(index * 137.508) % 360} 75% 70%)`;
  let hash = 0;
  for (const char of String(type))
    hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return `hsl(${hash % 360} 75% 70%)`;
}
export function typeGlyph(type) {
  const name = String(type);
  const words = name.match(/[A-Z]?[a-z\d]+|[A-Z]+(?![a-z])/g) || [];
  return (words.length > 1 ? words.map((word) => word[0]).join("") : name).slice(0, 2).toUpperCase();
}
export function interactions(snapshot, visible) {
  const edges = [];
  for (const plaza of snapshot?.plazas || []) {
    for (const link of plaza.links || []) {
      edges.push({
        ...link,
        from: agentKey(plaza.id, link.source),
        to: agentKey(plaza.id, link.target),
        crossPlaza: false,
      });
    }
  }
  for (const link of snapshot?.links || []) {
    edges.push({
      ...link,
      from: agentKey(link.source_plaza, link.source),
      to: agentKey(link.target_plaza, link.target),
      crossPlaza: true,
    });
  }
  return edges.filter((edge) => visible.has(edge.from) && visible.has(edge.to));
}
// A Plaza's authorized runtime is represented by its hub, rather than a second
// agent circle. Never infer identities from display names or unavailable peers.
export function topologyKey(snapshot, key) {
  for (const plaza of snapshot?.plazas || []) {
    if (plaza.plaza_agent_id &&
        plaza.agents?.some((a) => a.id === plaza.plaza_agent_id) &&
        key === agentKey(plaza.id, plaza.plaza_agent_id))
      return plazaKey(plaza.id);
  }
  return key;
}
export function topologyInteractions(snapshot, visible) {
  const authorized = new Set((snapshot?.plazas || []).flatMap((p) =>
    (p.agents || []).map((a) => agentKey(p.id, a.id))));
  return interactions(snapshot, authorized).map((edge) => ({
    ...edge, from: topologyKey(snapshot, edge.from), to: topologyKey(snapshot, edge.to),
  })).filter((edge) => visible.has(edge.from) && visible.has(edge.to));
}
export const edgeKey = (edge) => JSON.stringify([edge.from, edge.to]);
export function topologyEdges(snapshot) {
  const visible = new Set((snapshot?.plazas || []).flatMap((p) => [
    plazaKey(p.id), ...(p.agents || []).map((a) => topologyKey(snapshot, agentKey(p.id, a.id))),
  ]));
  return topologyInteractions(snapshot, visible);
}
/** Compare complete authorized snapshots, independent of graph filters. */
export class EdgeActivityTracker {
  constructor() { this.reset(); }
  reset() {
    this.context = null;
    this.seen = new Map();
    this.connected = new Set();
    this.cutoffs = new Map();
  }
  update(snapshot) {
    const context = JSON.stringify([snapshot?.user?.id || "", snapshot?.call_window_sec ?? null]);
    const baseline = this.context !== context;
    const scopes = new Map(), connected = new Set(), cutoffs = new Map();
    for (const plaza of snapshot?.plazas || []) {
      if (plaza.status !== "connected") continue;
      connected.add(plaza.id);
      cutoffs.set(plaza.id, Number(plaza.generated_at || snapshot.generated_at) || Infinity);
      scopes.set(plazaKey(plaza.id), plaza.id);
      for (const agent of plaza.agents || [])
        scopes.set(topologyKey(snapshot, agentKey(plaza.id, agent.id)), plaza.id);
    }
    const seen = new Map(), changed = new Set();
    for (const edge of topologyEdges(snapshot)) {
      const source = scopes.get(edge.from), target = scopes.get(edge.to);
      const at = Number(edge.last_seen);
      if (!connected.has(source) || !connected.has(target) || !Number.isFinite(at) || at <= 0) continue;
      const key = edgeKey(edge), sample = seen.get(key) || { at: 0, calls: 0, source, target };
      sample.at = Math.max(sample.at, at);
      sample.calls += Number.isSafeInteger(edge.calls) && edge.calls >= 0 ? edge.calls : 0;
      seen.set(key, sample);
    }
    for (const [key, sample] of seen) {
      // Newly connected sources establish a baseline rather than replaying history.
      if (baseline || !this.connected.has(sample.source) || !this.connected.has(sample.target)) continue;
      const previous = this.seen.get(key);
      const newCall = previous
        ? sample.at > previous.at || sample.at === previous.at && sample.calls > previous.calls
        : sample.at > Math.min(this.cutoffs.get(sample.source), this.cutoffs.get(sample.target));
      if (newCall) changed.add(key);
    }
    this.context = context;
    this.seen = seen;
    this.connected = connected;
    this.cutoffs = cutoffs;
    return changed;
  }
}
function origin(url) {
  try { return new URL(url).origin; } catch { return ""; }
}
/** Correlate only authorized endpoint jobs; never join unqualified IDs across Plazas. */
export function edgeDetails(snapshot, edge) {
  if (!edge || !topologyEdges(snapshot).some((e) => edgeKey(e) === edgeKey(edge))) return null;
  const agents = (snapshot.plazas || []).flatMap((p) => (p.agents || []).map((a) => ({
    ...a, source: p.id, plaza: p.name, key: topologyKey(snapshot, agentKey(p.id, a.id)),
  })));
  const source = agents.find((a) => a.key === edge.from),
    target = agents.find((a) => a.key === edge.to);
  if (!source || !target) return null;
  const events = (snapshot.plazas || []).flatMap((p) => (p.events || [])
    .filter((e) => e.event === "interaction" && e.direction === "outbound" && e.phase === "result" &&
      topologyKey(snapshot, agentKey(p.id, e.agent_id)) === edge.from &&
      e.peer_agent_id && topologyKey(snapshot, agentKey(e.peer_source || p.id, e.peer_agent_id)) === edge.to &&
      (p.calls_since == null || e.timestamp >= p.calls_since) &&
      (p.calls_until == null || e.timestamp <= p.calls_until))
    .map((e) => ({ ...e, source: p.id })))
    .sort((a, b) => b.timestamp - a.timestamp);
  const traces = new Set(events.map((e) => e.trace_id).filter(Boolean));
  const jobs = [];
  for (const owner of source.key === target.key ? [source] : [source, target]) {
    const plaza = snapshot.plazas.find((p) => p.id === owner.source);
    for (const job of owner.observability?.jobs || []) {
      const jobEvents = (plaza.events || []).filter((e) => e.agent_id === owner.id && e.job_id === job.id);
      const byJob = owner.key === source.key && events.some((e) => e.job_id && e.job_id === job.id);
      const byTrace = (job.trace_id && traces.has(job.trace_id)) || jobEvents.some((e) => e.trace_id && traces.has(e.trace_id));
      const byPeer = owner.key === source.key && job.peer_agent_id &&
        topologyKey(snapshot, agentKey(job.peer_source || owner.source, job.peer_agent_id)) === target.key;
      // HTTP jobs can be observed before a completed interaction exists. Only
      // resolve an origin when it identifies exactly one authorized endpoint.
      const peerOrigin = origin(job.peer_origin);
      const peers = peerOrigin ? agents.filter((a) => origin(a.endpoint) === peerOrigin) : [];
      const byOrigin = owner.key === source.key && peers.length === 1 && peers[0].key === target.key;
      if (byJob || byTrace || byPeer || byOrigin)
        jobs.push({ ...job, owner, association: byJob ? "Job ID" : byPeer || byOrigin ? "Peer endpoint" : "Shared trace" });
    }
  }
  return { source, target, events, jobs };
}
export function nodeCallCounts(snapshot) {
  const visible = new Set((snapshot?.plazas || []).flatMap((p) => [
    plazaKey(p.id), ...(p.agents || []).map((a) => topologyKey(snapshot, agentKey(p.id, a.id))),
  ]));
  const counts = new Map([...visible].map((id) => [id, 0]));
  for (const edge of topologyInteractions(snapshot, visible)) {
    if (!Number.isSafeInteger(edge.calls) || edge.calls < 0) continue;
    counts.set(edge.from, counts.get(edge.from) + edge.calls);
    if (edge.to !== edge.from) counts.set(edge.to, counts.get(edge.to) + edge.calls);
  }
  return counts;
}
export function matchesCallRange(calls, min = null, max = null) {
  return (min == null || calls >= min) && (max == null || calls <= max);
}
export function neighbors(selected, edges) {
  const result = new Set();
  for (const edge of edges) {
    if (edge.from === selected && edge.to !== selected) result.add(edge.to);
    if (edge.to === selected && edge.from !== selected) result.add(edge.from);
  }
  return result;
}
export function fitView(positions, ids, width, height, padding = 65) {
  const points = [...ids].map((id) => positions.get(id)).filter(Boolean);
  if (!points.length) return { zoom: 1, x: 0, y: 0 };
  const minX = Math.min(...points.map((p) => p.x)) - padding;
  const maxX = Math.max(...points.map((p) => p.x)) + padding;
  const minY = Math.min(...points.map((p) => p.y)) - padding;
  const maxY = Math.max(...points.map((p) => p.y)) + padding;
  return fitBounds(
    { x: minX, y: minY, width: maxX - minX, height: maxY - minY },
    width,
    height,
    0,
  );
}
export function fitBounds(bounds, width, height, padding = 8) {
  const spanX = Math.max(1, bounds.width + padding * 2),
    spanY = Math.max(1, bounds.height + padding * 2);
  // A widely dragged node must still fit; manual zoom's lower limit is not a fit limit.
  const zoom = Math.min(
    2.5,
    Math.max(1, width) / spanX,
    Math.max(1, height) / spanY,
  );
  return {
    zoom,
    x: width / 2 - (bounds.x + bounds.width / 2) * zoom,
    y: height / 2 - (bounds.y + bounds.height / 2) * zoom,
  };
}
export function edgeShape(a, b, self = false) {
  if (self)
    return {
      path: `M ${a.x - 18} ${a.y - 20} C ${a.x - 78} ${a.y - 105}, ${a.x + 78} ${a.y - 105}, ${a.x + 18} ${a.y - 20}`,
      x: a.x,
      y: a.y - 82,
    };
  const dx = b.x - a.x,
    dy = b.y - a.y,
    distance = Math.max(1, Math.hypot(dx, dy));
  const ux = dx / distance,
    uy = dy / distance;
  const curve = Math.min(35, distance * 0.15);
  const cx = (a.x + b.x) / 2 - uy * curve,
    cy = (a.y + b.y) / 2 + ux * curve;
  return {
    path: `M ${a.x + ux * 30} ${a.y + uy * 30} Q ${cx} ${cy} ${b.x - ux * 35} ${b.y - uy * 35}`,
    x: (a.x + 2 * cx + b.x) / 4,
    y: (a.y + 2 * cy + b.y) / 4 - 6,
  };
}

/** Bounded force simulation. Nodes dragged by the user stay pinned until relayout. */
export class ForceLayout {
  constructor(agents, plazas, edges, positions = new Map(), density = 50) {
    this.positions = positions;
    this.nodes = [];
    this.edges = edges;
    this.alpha = 1;
    this.spacing = this.densitySpacing(density);
    const spacing = Math.max(700, Math.sqrt(agents.length) * 85);
    this.plazaSpacing = spacing;
    for (const [index, plaza] of plazas.entries()) {
      const id = plazaKey(plaza.id);
      const hub = positions.get(id) || {
        x: index * spacing * this.spacing + 450,
        y: 450,
        fixed: true,
      };
      positions.set(id, hub);
      this.nodes.push({ id, p: hub, hub: true, index });
      const members = agents.filter((agent) => agent.source === plaza.id);
      for (const [j, agent] of members.entries()) {
        const angle = j * 2.399963229728653;
        const radius = (110 + Math.sqrt(j + 1) * 53) * this.spacing;
        const p = positions.get(agent.key) || {
          x: hub.x + Math.cos(angle) * radius,
          y: hub.y + Math.sin(angle) * radius,
        };
        p.vx = p.vy = 0;
        positions.set(agent.key, p);
        this.nodes.push({ id: agent.key, p, anchor: hub });
      }
    }
    this.active = new Map(this.nodes.map((node) => [node.id, node]));
  }
  densitySpacing(density) {
    return 2 ** ((50 - (Number.isFinite(density) ? Math.max(0, Math.min(100, density)) : 50)) / 50);
  }
  setDensity(density) {
    const spacing = this.densitySpacing(density), ratio = spacing / this.spacing;
    if (spacing === this.spacing) return;
    const previous = new Map(this.nodes.filter((node) => node.hub).map(({ p }) => [p, { x: p.x, y: p.y }]));
    for (const { p, hub, index } of this.nodes) {
      if (hub && !p.pinned) p.x = 450 + index * this.plazaSpacing * spacing;
    }
    for (const { p, anchor } of this.nodes) {
      if (!anchor || p.fixed) continue;
      const old = previous.get(anchor);
      p.x = anchor.x + (p.x - old.x) * ratio;
      p.y = anchor.y + (p.y - old.y) * ratio;
      p.vx = p.vy = 0;
    }
    this.spacing = spacing;
    this.alpha = 1;
  }
  tick(steps = 1) {
    for (let step = 0; step < steps; step++) {
      const alpha = this.alpha;
      for (let i = 0; i < this.nodes.length; i++) {
        const a = this.nodes[i].p;
        for (let j = i + 1; j < this.nodes.length; j++) {
          const b = this.nodes[j].p;
          const dx = b.x - a.x || 0.01,
            dy = b.y - a.y || 0.01;
          const distance = Math.max(1, Math.hypot(dx, dy));
          const minimum = (this.nodes[i].hub ? 72 : 30) + (this.nodes[j].hub ? 72 : 30) + 23 * this.spacing;
          const force =
            (2600 * this.spacing ** 3 / (distance * distance) + Math.max(0, minimum - distance) * 0.13) *
            alpha;
          const fx = (dx / distance) * force,
            fy = (dy / distance) * force;
          if (!a.fixed) {
            a.vx -= fx;
            a.vy -= fy;
          }
          if (!b.fixed) {
            b.vx += fx;
            b.vy += fy;
          }
        }
      }
      for (const edge of this.edges) {
        const a = this.active.get(edge.from)?.p,
          b = this.active.get(edge.to)?.p;
        if (!a || !b || a === b) continue;
        const dx = b.x - a.x,
          dy = b.y - a.y,
          distance = Math.max(1, Math.hypot(dx, dy));
        const force = (distance - 125 * this.spacing) * 0.035 * alpha;
        if (!a.fixed) {
          a.vx += (dx / distance) * force;
          a.vy += (dy / distance) * force;
        }
        if (!b.fixed) {
          b.vx -= (dx / distance) * force;
          b.vy -= (dy / distance) * force;
        }
      }
      for (const { p, anchor } of this.nodes) {
        if (p.fixed) continue;
        p.vx += (anchor.x - p.x) * 0.004 * alpha;
        p.vy += (anchor.y - p.y) * 0.004 * alpha;
        const speed = 12 * this.spacing;
        p.vx = Math.max(-speed, Math.min(speed, p.vx)) * 0.72;
        p.vy = Math.max(-speed, Math.min(speed, p.vy)) * 0.72;
        p.x += p.vx;
        p.y += p.vy;
      }
      this.alpha *= 0.96;
    }
    return this.alpha > 0.012;
  }
}
