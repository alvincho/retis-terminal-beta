import {
  agentKey,
  plazaKey,
  typeColor,
  typeGlyph,
  interactions,
  topologyKey,
  topologyInteractions,
  topologyEdges,
  EdgeActivityTracker,
  edgeKey,
  edgeDetails,
  nodeCallCounts,
  matchesCallRange,
  neighbors,
  fitView,
  fitBounds,
  edgeShape,
  ForceLayout,
} from "./graph.js?v=f13727dd98b1";
import { JobsPanel, jobFilters, isRequest } from "./jobs.js";
import { JobLogsPopup, renderLogEntries } from "./job-logs.js";
const $ = (s) => document.querySelector(s);
const state = {
  data: null,
  source: "all",
  health: "all",
  types: [],
  minCalls: null,
  maxCalls: null,
  callWindowMinutes: null,
  callWindowUnit: "minutes",
  density: 50,
  callCounts: new Map(),
  sourceRevision: 0,
  neighborsOnly: false,
  search: "",
  selected: "",
  selectedEdge: "",
  jobFilters: jobFilters(),
  tab: "jobs",
  trace: "",
  positions: new Map(),
  zoom: 1,
  x: 0,
  y: 0,
  logs: [],
  logRequest: null,
  tail: false,
  loading: false,
  callAnimations: false,
};
const callActivity = new EdgeActivityTracker(), activeCalls = new Map();
const callMotion = matchMedia("(prefers-reduced-motion: reduce)");
let callAnimationFrame = null;
const callPulseDuration = 1800;
const jobsPanel = new JobsPanel({
  api, filters: () => state.jobFilters, token: () => session().access_token,
  changed: (value) => { state.jobFilters = jobFilters(value); preferences(); },
  actions: edgeActions,
});
const jobLogs = new JobLogsPopup({
  api, token: () => session().access_token,
  allowed: (owner) => !!owner && allAgents().some((agent) => agent.source === owner.source && agent.id === owner.id && agent.logs_allowed),
});
const svgNS = "http://www.w3.org/2000/svg";
const key = agentKey;
let pitColors = new Map();
const colorForType = (type) => pitColors.get(type) || typeColor(type);
function el(tag, text, cls) {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = String(text);
  if (cls) n.className = cls;
  return n;
}
function svg(tag, attrs = {}) {
  const n = document.createElementNS(svgNS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
}
function session() {
  try {
    return (
      JSON.parse(localStorage.getItem("plaza.ui.authSession") || "null") || {}
    );
  } catch {
    return {};
  }
}
async function api(path, body, method) {
  const token = session().access_token;
  const r = await fetch("/api/observability" + path, {
    method: method || (body ? "POST" : "GET"),
    headers: {
      ...(token ? { Authorization: "Bearer " + token } : {}),
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
    cache: "no-store",
  });
  let v;
  try {
    v = await r.json();
  } catch {
    throw Error("Plaza returned an invalid response.");
  }
  if (!r.ok) {
    const e = Error(typeof v.detail === "string" ? v.detail : "Request failed");
    e.status = r.status;
    throw e;
  }
  return v;
}
function notice(text) {
  $("#notice").textContent = text || "";
}
function badge(text, cls = "") {
  return el("span", text, "badge " + cls);
}
function time(value) {
  if (!value) return "—";
  const d = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(+d)
    ? "—"
    : d.toLocaleTimeString(undefined, { hour12: false });
}
function ago(sec) {
  return sec == null
    ? "Never"
    : sec < 60
      ? Math.round(sec) + "s ago"
      : sec < 3600
        ? Math.round(sec / 60) + "m ago"
        : Math.round(sec / 3600) + "h ago";
}
function number(value) {
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 });
}
function health(a) {
  return a.connectivity === "online" && !a.telemetry_stale
    ? a.observability.health?.status || "unknown"
    : "unknown";
}
function allAgents() {
  return (state.data?.plazas || []).flatMap((p) =>
    p.agents.map((a) => ({
      ...a,
      source: p.id,
      plaza: p.name,
      key: key(p.id, a.id),
    })),
  );
}
function matchingAgents() {
  return allAgents().filter(
    (a) =>
      (state.source === "all" || a.source === state.source) &&
      (!state.search ||
        a.name.toLowerCase().includes(state.search.trim().toLowerCase())) &&
      (!state.types.length || state.types.includes(a.type)) &&
      matchesCallRange(callsFor(nodeKey(a)), state.minCalls, state.maxCalls) &&
      (state.health === "all" ||
        state.health === a.connectivity ||
        (state.health === "busy" && a.observability.activity === "busy") ||
        (state.health === "attention" &&
          (a.connectivity !== "online" ||
            ["degraded", "unhealthy"].includes(health(a))))),
  );
}
function scopedPlazas() {
  return (state.data?.plazas || []).filter(
    (p) => state.source === "all" || p.id === state.source,
  );
}
const nodeKey = (a) => topologyKey(state.data, a.key);
const callsFor = (id) => state.callCounts.get(id) || 0;
function callPeriod() {
  const seconds = state.data?.call_window_sec;
  return seconds ? "past " + (seconds % 3600 === 0 ? seconds / 3600 + "h" : seconds / 60 + "m") : "retained history";
}
function visiblePlazas() {
  return scopedPlazas().filter((p) => matchesCallRange(callsFor(plazaKey(p.id)), state.minCalls, state.maxCalls));
}
function graphContext(agents) {
  const plazas = visiblePlazas();
  const visible = new Set([
    ...agents.map(nodeKey), ...plazas.map((p) => plazaKey(p.id)),
  ]);
  const links = topologyInteractions(state.data, visible);
  const adjacent = neighbors(state.selected, links);
  // Registration connections are structural; they are not observed calls.
  for (const a of agents) {
    const hub = plazaKey(a.source), id = nodeKey(a);
    if (hub === id || !visible.has(hub)) continue;
    if (state.selected === hub) adjacent.add(id);
    if (state.selected === id) adjacent.add(hub);
  }
  return { plazas, visible, links, adjacent };
}
function filtered() {
  const agents = matchingAgents();
  if (!state.neighborsOnly)
    return agents;
  const { visible, adjacent } = graphContext(agents);
  if (!visible.has(state.selected)) return agents;
  return agents.filter((a) => nodeKey(a) === state.selected || adjacent.has(nodeKey(a)));
}
function renderTypeFilters() {
  const registeredTypes = [...new Set(allAgents().map((a) => a.type))];
  pitColors = new Map(registeredTypes.map((type) => [type, typeColor(type, registeredTypes)]));
  const counts = new Map();
  for (const a of allAgents()) {
    if (state.source === "all" || a.source === state.source)
      counts.set(a.type, (counts.get(a.type) || 0) + 1);
  }
  // Keep selections when their type is absent in another Plaza or a source is
  // temporarily unavailable. The user can still remove those zero-count types.
  for (const type of state.types) if (!counts.has(type)) counts.set(type, 0);
  const options = $("#pit-type-options");
  const focused = options.contains(document.activeElement)
    ? document.activeElement.dataset.pitType : null;
  const focusedAll = document.activeElement.id === "pit-types-all";
  const scroll = options.scrollTop;
  options.replaceChildren();
  const label = $("#pit-type-label");
  label.textContent = !state.types.length ? "All PIT types"
    : state.types.length <= 2 ? state.types.join(", ") : state.types.length + " PIT types";
  label.title = state.types.join(", ") || "All PIT types";
  const all = el("button", "All PIT types");
  all.id = "pit-types-all";
  all.setAttribute("aria-pressed", String(!state.types.length));
  all.onclick = () => { state.types = []; filtersChanged(); };
  options.append(all);
  const legend = $("#type-legend");
  legend.title =
    "Authorized agents on " +
    (state.source === "all"
      ? "all Plazas"
      : state.data?.plazas.find((p) => p.id === state.source)?.name ||
        "selected Plaza");
  legend.replaceChildren();
  for (const [type, count] of [...counts].sort(([a], [b]) =>
    a.localeCompare(b),
  )) {
    const option = el("label");
    option.style.setProperty("--type-color", colorForType(type));
    const checkbox = el("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.types.includes(type);
    checkbox.dataset.pitType = type;
    checkbox.setAttribute("aria-label", "Include " + type);
    checkbox.onchange = () => toggleType(type);
    option.append(checkbox, el("span", type, "type-name"), el("span", String(count), "type-count"));
    options.append(option);
    const button = el(
      "button",
      `${type} ${count}`,
      state.types.includes(type) ? "active" : "",
    );
    button.style.setProperty("--type-color", colorForType(type));
    button.setAttribute("aria-pressed", String(state.types.includes(type)));
    button.onclick = () => toggleType(type);
    legend.append(button);
  }
  options.scrollTop = scroll;
  if (focusedAll) all.focus({ preventScroll: true });
  if (focused != null) {
    const target = [...options.querySelectorAll("input")].find((input) => input.dataset.pitType === focused);
    target?.focus({ preventScroll: true });
  }
}
function toggleType(type) {
  state.types = state.types.includes(type)
    ? state.types.filter((selected) => selected !== type) : [...state.types, type].sort();
  filtersChanged();
}
function filtersChanged() {
  const context = graphContext(matchingAgents());
  if ((state.selected && !context.visible.has(state.selected)) ||
      (state.selectedEdge && !context.links.some((e) => edgeKey(e) === state.selectedEdge))) {
    state.neighborsOnly = false;
    pick("");
  }
  renderTypeFilters();
  preferences();
  render();
  fitGraph();
}
function selected() {
  const plaza = scopedPlazas().find((p) => plazaKey(p.id) === state.selected);
  if (!plaza) return allAgents().find((a) => a.key === state.selected);
  const runtime = allAgents().find((a) => a.source === plaza.id && a.id === plaza.plaza_agent_id);
  return {
    ...runtime, key: plazaKey(plaza.id), source: plaza.id, plaza: plaza.name,
    name: plaza.name, type: "Plaza", isPlaza: true, connection: plaza,
    id: runtime?.id || "", endpoint: runtime?.endpoint || plaza.url || location.origin,
    connectivity: plaza.status === "connected" ? runtime?.connectivity || "online" : "offline",
    observability: runtime?.observability || {}, telemetry_supported: !!runtime?.telemetry_supported,
    telemetry_stale: runtime?.telemetry_stale, logs_allowed: !!runtime?.logs_allowed,
  };
}
let preferenceTimer,
  preferenceWrite = Promise.resolve();
function preferences() {
  clearTimeout(preferenceTimer);
  const owner = state.data?.user.id,
    token = session().access_token;
  if (!owner || !token) return;
  preferenceTimer = setTimeout(() => {
    const value = {
      source: state.source,
      health: state.health,
      types: [...state.types],
      minCalls: state.minCalls,
      maxCalls: state.maxCalls,
      callWindowMinutes: state.callWindowMinutes,
      callWindowUnit: state.callWindowUnit,
      density: state.density,
      jobFilters: { ...state.jobFilters },
      search: state.search,
      tab: state.tab,
    };
    // Serialize writes: slower earlier requests must not overwrite the latest filters.
    preferenceWrite = preferenceWrite
      .catch(() => {})
      .then(() => {
        if (state.data?.user.id !== owner || session().access_token !== token)
          return;
        return api("/preferences", value, "PUT");
      })
      .catch((e) => {
        if (state.data?.user.id === owner && session().access_token === token)
          notice("Could not save dashboard preferences. " + e.message);
      });
  }, 400);
}
let snapshotLoad = null;
function load() {
  if (!snapshotLoad) snapshotLoad = loadSnapshot().finally(() => { snapshotLoad = null; });
  return snapshotLoad;
}
async function reloadSources() {
  if (snapshotLoad) await snapshotLoad;
  await load();
}
async function loadSnapshot() {
  const revision = state.sourceRevision;
  const accountToken = session().access_token;
  const windowMinutes = state.callWindowMinutes;
  state.loading = true;
  $("#refresh").disabled = true;
  try {
    const next = await api("/snapshot" + (windowMinutes == null ? "" : "?call_window_sec=" + windowMinutes * 60));
    if (revision !== state.sourceRevision || accountToken !== session().access_token || windowMinutes !== state.callWindowMinutes) return;
    if ((next.call_window_sec ?? null) !== (windowMinutes == null ? null : windowMinutes * 60))
      throw Error("Plaza could not apply the call time range. Retry Refresh.");
    if (state.data && state.data.user.id !== next.user.id) {
      jobLogs.close();
      $("#connection-dialog").close();
      $("#connection-list").replaceChildren();
      $("#add-plaza-form").reset();
      $("#add-plaza-status").textContent = "";
      state.selected = "";
      state.selectedEdge = "";
      state.neighborsOnly = false;
      const prefs = await api("/preferences");
      if (revision !== state.sourceRevision || accountToken !== session().access_token) return;
      restoreCallRange(prefs);
      state.jobFilters = jobFilters(prefs.jobFilters);
      restoreCallWindow(prefs);
      restoreDensity(prefs.density);
      state.positions.clear();
      layoutModel = null;
      dragging = null;
      state.logs = [];
      state.logRequest = null;
      state.tail = false;
      state.trace = "";
      $("#inspector").replaceChildren(
        el("div", "Select an agent to inspect its status.", "empty"),
      );
    }
    if (state.data && (state.data.user.id !== next.user.id || state.data.call_window_sec !== next.call_window_sec))
      stopCallPulses();
    state.data = next;
    observeCallActivity(next);
    jobLogs.revalidate();
    if (state.source !== "all" && !next.plazas.some((p) => p.id === state.source)) {
      state.source = "all";
      preferences();
    }
    $("#login").hidden = true;
    $("#account").textContent = state.data.user.name;
    $("#updated").textContent = "Updated " + time(state.data.generated_at);
    notice("");
    render();
    if (windowMinutes !== state.callWindowMinutes) {
      $("#call-window-status").textContent = "Updating calls…";
      setTimeout(reloadSources, 0);
    } else $("#call-window-status").textContent = "";
  } catch (e) {
    if (windowMinutes !== state.callWindowMinutes || accountToken !== session().access_token) return;
    notice(e.message);
    if ((state.data?.call_window_sec ?? null) !== (windowMinutes == null ? null : windowMinutes * 60))
      $("#call-window-status").textContent = "Time range not updated; retry Refresh.";
    if (e.status === 401 || e.status === 403) {
      jobLogs.close();
      $("#connection-dialog").close();
      $("#connection-list").replaceChildren();
      $("#add-plaza-form").reset();
      $("#add-plaza-status").textContent = "";
      state.data = null;
      setCallAnimations(false);
      state.types = [];
      restoreCallRange({});
      restoreCallWindow({});
      restoreDensity();
      state.selected = "";
      state.selectedEdge = "";
      state.jobFilters = jobFilters();
      jobsPanel.cancel();
      state.neighborsOnly = false;
      state.positions.clear();
      layoutModel = null;
      dragging = null;
      state.logs = [];
      state.logRequest = null;
      state.tail = false;
      $("#account").textContent = "";
      $("#inspector").replaceChildren(
        el("div", "Sign in to inspect monitored agents.", "empty"),
      );
      renderTypeFilters();
      renderSources();
      renderSummary();
      renderGraph();
      renderTable();
      $("#login").hidden = false;
      $("#updated").textContent = "Sign-in required";
    }
  } finally {
    state.loading = false;
    $("#refresh").disabled = false;
  }
}
function render() {
  state.callCounts = nodeCallCounts(state.data);
  $("#calls-column").title = "Incoming and outgoing calls · " + callPeriod();
  $("#calls-column").textContent = "Calls" + (state.data?.call_window_sec ? " · " + callPeriod() : "");
  $("#count-calls").title = "Observed calls · " + callPeriod();
  renderTypeFilters();
  renderSources();
  renderSummary();
  renderGraph();
  renderTable();
  renderInspector();
}
function renderSources() {
  const container = $("#sources");
  container.replaceChildren();
  const total = allAgents().length;
  const options = [
    {
      id: "all",
      name: "All Plazas",
      status: "connected",
      agents: Array(total),
    },
    ...(state.data?.plazas || []),
  ];
  for (const p of options) {
    const b = el(
      "button",
      undefined,
      "source" + (state.source === p.id ? " active" : ""),
    );
    b.append(
      el("span", p.name, "source-name"),
      el("b", p.agents.length),
      el(
        "small",
        p.id === "all"
          ? options.length - 1 + " environments"
          : p.status === "connected"
            ? "Connected"
            : p.status === "disconnected"
              ? "Approval required"
              : "Unavailable",
      ),
    );
    b.setAttribute("aria-pressed", String(state.source === p.id));
    b.onclick = () => {
      state.source = p.id;
      filtersChanged();
    };
    container.append(b);
  }
  $("#scope-label").textContent =
    options.find((p) => p.id === state.source)?.name || "All Plazas";
}
function renderSummary() {
  const agents = filtered();
  $("#count-agents").textContent = agents.length;
  $("#count-online").textContent = agents.filter(
    (a) => a.connectivity === "online",
  ).length;
  $("#count-jobs").textContent = agents.reduce(
    (n, a) => n + (a.observability.jobs || []).filter((job) => !isRequest(job)).length,
    0,
  );
  $("#count-alerts").textContent = agents.filter(
    (a) =>
      a.connectivity !== "online" ||
      ["unhealthy", "degraded"].includes(health(a)),
  ).length;
  $("#count-calls").textContent = interactions(
    state.data,
    new Set(agents.map((a) => a.key)),
  ).reduce((n, e) => n + e.calls, 0);
}
function pick(agentKey) {
  jobsPanel.cancel();
  state.selectedEdge = "";
  agentKey = topologyKey(state.data, agentKey);
  if (state.selected !== agentKey) {
    state.logs = [];
    state.logRequest = null;
    state.tail = false;
    state.trace = "";
  }
  state.selected = agentKey;
  if (agentKey.startsWith("@plaza:")) state.tab = "connection";
  else if (state.tab === "connection") state.tab = "jobs";
  if (!agentKey) state.neighborsOnly = false;
  renderSummary();
  renderGraph();
  renderTable();
  renderInspector(true);
}
function selectedEdge() {
  return topologyEdges(state.data).find((e) => edgeKey(e) === state.selectedEdge);
}
function pickEdge(link) {
  jobsPanel.cancel();
  state.selected = "";
  state.selectedEdge = edgeKey(link);
  state.neighborsOnly = false;
  state.tab = "metrics";
  state.trace = "";
  state.logs = [];
  state.logRequest = null;
  state.tail = false;
  renderSummary();
  renderTable();
  renderGraph();
  renderInspector(true);
}
function renderTable() {
  const agents = filtered();
  const { adjacent } = graphContext(agents);
  $("#list-count").textContent = agents.length + " agents";
  const tbody = $("#agents");
  tbody.replaceChildren();
  for (const a of agents) {
    const row = el(
      "tr",
      undefined,
      nodeKey(a) === state.selected
        ? "selected"
        : adjacent.has(nodeKey(a))
          ? "neighbor"
          : "",
    );
    row.tabIndex = 0;
    row.dataset.callCount = callsFor(nodeKey(a));
    row.setAttribute("aria-label", "Inspect " + a.name + " on " + a.plaza);
    row.onclick = () => pick(a.key);
    row.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        pick(a.key);
      }
    };
    const name = el("td");
    name.append(el("span", a.name), el("small", a.plaza + " / " + a.type));
    const connection = el("td");
    connection.append(
      badge(
        a.connectivity,
        a.connectivity === "online" ? "healthy" : "unknown",
      ),
    );
    const h = el("td");
    h.append(badge(health(a), health(a)));
    row.append(
      name,
      connection,
      h,
      el(
        "td",
        a.telemetry_supported
          ? a.observability.activity
          : a.registration_state === "not_registered"
            ? "Not connected"
            : "Legacy agent",
      ),
      el("td", (a.observability.jobs || []).filter((job) => !isRequest(job)).length),
      el("td", number(callsFor(nodeKey(a)))),
      el("td", ago(a.heartbeat_age_sec)),
    );
    tbody.append(row);
  }
  if (!agents.length) {
    const cell = el("td", "No monitored agents match this view.", "empty");
    cell.colSpan = 7;
    const row = el("tr");
    row.append(cell);
    tbody.append(row);
  }
}
function graphTransform() {
  $("#graph-world").setAttribute(
    "transform",
    `translate(${state.x} ${state.y}) scale(${state.zoom})`,
  );
}
let graphNodes = new Map(),
  graphEdges = [],
  layoutModel = null,
  graphAnimation = null;
let dragging = null,
  dragMoved = false,
  deferredGraphRender = false;
function graphViewport() {
  const box = $("#topology").getBoundingClientRect();
  return {
    width: 1000,
    height: Math.max(1, (1000 * box.height) / Math.max(1, box.width)),
  };
}
function fitGraph(ids = null) {
  const view = graphViewport(),
    box = $("#topology").getBoundingClientRect();
  $("#topology").setAttribute("viewBox", `0 0 ${view.width} ${view.height}`);
  const scale = view.width / Math.max(1, box.width);
  const top = ($(".graph-heading").getBoundingClientRect().bottom - box.top + 12) * scale;
  const bottom =
    ($(".graph-bottom").getBoundingClientRect().height + 20) * scale;
  const left = 16 * scale,
    width = Math.max(1, view.width - left * 2),
    height = Math.max(1, view.height - top - bottom);
  const fit = ids
    ? fitView(state.positions, ids, width, height, 70)
    : fitBounds($("#graph-world").getBBox(), width, height);
  state.zoom = fit.zoom;
  state.x = fit.x + left;
  state.y = fit.y + top;
  graphTransform();
}
function fitEntireGraph() {
  const resetWindow = state.callWindowMinutes != null;
  state.source = "all";
  state.types = [];
  state.health = "all";
  state.search = "";
  restoreCallRange({});
  restoreCallWindow({});
  state.neighborsOnly = false;
  $("#search").value = "";
  $("#health").value = "all";
  pick("");
  render();
  preferences();
  fitGraph();
  if (resetWindow) refreshCallWindow();
}
function updateGraphPositions() {
  for (const [id, node] of graphNodes) {
    const p = state.positions.get(id);
    if (p) node.setAttribute("transform", `translate(${p.x} ${p.y})`);
  }
  for (const edge of graphEdges) {
    const a = state.positions.get(edge.from),
      b = state.positions.get(edge.to);
    if (!a || !b) continue;
    if (edge.path.tagName === "line") {
      for (const [name, value] of Object.entries({
        x1: a.x,
        y1: a.y,
        x2: b.x,
        y2: b.y,
      }))
        edge.path.setAttribute(name, value);
    } else {
      const shape = edgeShape(a, b, edge.from === edge.to);
      edge.path.setAttribute("d", shape.path);
      if (edge.hit) edge.hit.setAttribute("d", shape.path);
      edge.label.setAttribute("x", shape.x);
      edge.label.setAttribute("y", shape.y);
    }
  }
  graphTransform();
}
function animateGraph() {
  cancelAnimationFrame(graphAnimation);
  if (!layoutModel || matchMedia("(prefers-reduced-motion: reduce)").matches)
    return;
  function frame() {
    if (document.hidden) {
      graphAnimation = null;
      return;
    }
    const moving = layoutModel.tick(2);
    updateGraphPositions();
    graphAnimation = moving ? requestAnimationFrame(frame) : null;
  }
  graphAnimation = requestAnimationFrame(frame);
}
function renderCallControl() {
  const button = $("#call-play");
  button.disabled = !state.data;
  button.setAttribute("aria-pressed", String(state.callAnimations));
  button.setAttribute("aria-label", state.callAnimations ? "Pause call animations" : "Play call animations");
  button.title = state.callAnimations ? "Pause new-call animations" : "Play new-call animations · follows dashboard refreshes";
}
function stopCallPulses() {
  cancelAnimationFrame(callAnimationFrame);
  callAnimationFrame = null;
  activeCalls.clear();
  for (const edge of graphEdges) {
    edge.group?.classList.remove("call-active");
    edge.pulse?.remove();
    edge.pulse = null;
  }
}
function setCallAnimations(enabled) {
  state.callAnimations = Boolean(enabled && state.data);
  stopCallPulses();
  callActivity.reset();
  if (state.data) callActivity.update(state.data);
  renderCallControl();
}
function observeCallActivity(snapshot) {
  const changed = callActivity.update(snapshot);
  const allowed = new Set(topologyEdges(snapshot).map(edgeKey));
  for (const key of activeCalls.keys()) if (!allowed.has(key)) activeCalls.delete(key);
  if (!state.callAnimations || document.hidden) return;
  const started = performance.now();
  for (const key of changed) {
    activeCalls.set(key, started);
    // One pulse per edge per refresh, with bounded work during traffic bursts.
    if (activeCalls.size > 80) activeCalls.delete(activeCalls.keys().next().value);
  }
}
function animateCalls() {
  if (callAnimationFrame != null || !state.callAnimations || !activeCalls.size || document.hidden) return;
  function frame(now) {
    callAnimationFrame = null;
    if (!state.callAnimations || document.hidden) { stopCallPulses(); return; }
    for (const [key, started] of activeCalls)
      if (now - started >= callPulseDuration) activeCalls.delete(key);
    for (const edge of graphEdges) {
      if (!edge.group) continue;
      const started = activeCalls.get(edge.key), active = started != null;
      edge.group.classList.toggle("call-active", active);
      if (!active || callMotion.matches) {
        edge.pulse?.remove();
        edge.pulse = null;
        continue;
      }
      if (!edge.pulse) {
        edge.pulse = svg("circle", { class: "call-pulse", "aria-hidden": "true" });
        edge.group.append(edge.pulse);
      }
      const progress = Math.max(0, Math.min(1, (now - started) / callPulseDuration));
      const point = edge.path.getPointAtLength(edge.path.getTotalLength() * progress);
      edge.pulse.setAttribute("cx", point.x);
      edge.pulse.setAttribute("cy", point.y);
      edge.pulse.setAttribute("r", 4 / state.zoom);
    }
    if (activeCalls.size) callAnimationFrame = requestAnimationFrame(frame);
  }
  callAnimationFrame = requestAnimationFrame(frame);
}
$("#call-play").onclick = () => setCallAnimations(!state.callAnimations);
document.addEventListener("visibilitychange", () => {
  stopCallPulses();
  callActivity.reset();
});
function renderGraph() {
  renderCallControl();
  if (dragging) {
    deferredGraphRender = true;
    return;
  }
  cancelAnimationFrame(graphAnimation);
  const world = $("#graph-world");
  const focusedEdge = document.activeElement?.closest(".communication")?.getAttribute("data-edge-key");
  world.replaceChildren();
  graphNodes = new Map();
  graphEdges = [];
  const listedAgents = filtered(),
    agents = listedAgents.filter((a) => nodeKey(a) === a.key);
  const { plazas, visible, links, adjacent } = graphContext(listedAgents);
  const activeSelection = visible.has(state.selected) ? state.selected : "";
  const activeEdge = links.find((e) => edgeKey(e) === state.selectedEdge);
  if (state.selectedEdge && !activeEdge) {
    state.selectedEdge = "";
    renderInspector(true);
  }
  const endpoints = new Set(activeEdge ? [activeEdge.from, activeEdge.to] : []);
  const hasSelection = Boolean(activeSelection || activeEdge);
  const contextPlazas = new Set(
    agents
      .filter((a) => nodeKey(a) === activeSelection || adjacent.has(nodeKey(a)))
      .map((a) => a.source),
  );
  const signature = JSON.stringify([
    agents.map((a) => a.key).sort(),
    plazas.map((p) => p.id).sort(),
    links.map((e) => [e.from, e.to]).sort(),
  ]);
  const changed = !layoutModel || state.graphLayout !== signature;
  if (changed) {
    state.graphLayout = signature;
    const retained = new Set([
      ...allAgents().map((a) => a.key),
      ...(state.data?.plazas || []).map((p) => plazaKey(p.id)),
    ]);
    for (const id of state.positions.keys())
      if (!retained.has(id)) state.positions.delete(id);
    // Matching members keep layout anchors when their Plaza hub is filtered
    // out. Hidden anchors are never drawn or included in Fit graph.
    layoutModel = new ForceLayout(agents, scopedPlazas(), links, state.positions, state.density);
    layoutModel.tick(90);
  }
  $("#graph-empty").hidden = agents.length > 0 || plazas.length > 0;
  const edgeLayer = svg("g"),
    nodeLayer = svg("g");
  world.append(edgeLayer, nodeLayer);
  for (const plaza of plazas) {
    const runtime = allAgents().find((a) => a.source === plaza.id && a.id === plaza.plaza_agent_id);
    const plazaHealth = runtime ? health(runtime) : "unknown";
    const id = plazaKey(plaza.id),
      node = svg("g", {
        class:
          "plaza-node " +
          (plaza.status || "connected") + " " + plazaHealth +
          (id === activeSelection ? " selected" : endpoints.has(id) || adjacent.has(id) || contextPlazas.has(plaza.id)
            ? " neighbor" : hasSelection ? " muted-node" : ""),
        tabindex: "0",
        role: "button",
        "aria-label": "Select " + plaza.name,
        "data-node-key": id,
        "data-call-count": callsFor(id),
        "aria-pressed": String(id === activeSelection),
      });
    const label = svg("text", { y: 4 });
    label.textContent = plaza.name;
    const title = svg("title");
    title.textContent =
      plaza.name +
      " · Plaza · " +
      plaza.status +
      " · " + number(callsFor(id)) + " observed calls · " + callPeriod() +
      (plaza.error ? " · " + plaza.error : "");
    const status = svg("text", { y: 40, class: "plaza-status" });
    status.textContent =
      plaza.status === "connected"
        ? agents.filter((a) => a.source === plaza.id).length + " agents · " + plazaHealth
        : plaza.status === "disconnected"
          ? "Approval required"
          : "Unavailable";
    node.append(
      svg("rect", { x: -62, y: -24, width: 124, height: 48 }),
      label,
      status,
      title,
    );
    node.onclick = () => { if (!dragMoved) pick(id); };
    node.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        pick(id);
      }
    };
    attachDrag(node, id);
    nodeLayer.append(node);
    graphNodes.set(id, node);
  }
  for (const a of agents) {
    const selected = a.key === activeSelection,
      neighbor = adjacent.has(a.key) || endpoints.has(a.key);
    if (visible.has(plazaKey(a.source))) {
      const line = svg("line", {
        class:
          "membership" +
          (hasSelection
            ? selected || neighbor
              ? " highlight"
              : " muted-edge"
            : ""),
      });
      edgeLayer.append(line);
      graphEdges.push({ from: plazaKey(a.source), to: a.key, path: line });
    }
    const node = svg("g", {
      class:
        "node " +
        health(a) +
        (agents.length > 35 ? " dense" : "") +
        (a.connectivity !== "online" ? " offline" : "") +
        (selected
          ? " selected"
          : neighbor
            ? " neighbor"
            : hasSelection
              ? " muted-node"
              : ""),
      tabindex: "0",
      role: "button",
      "aria-pressed": String(selected),
      "aria-label": "Inspect " + a.name + " on " + a.plaza,
      "data-node-key": a.key,
      "data-pit-type": a.type,
      "data-call-count": callsFor(a.key),
    });
    node.style.setProperty("--node-color", colorForType(a.type));
    node.append(
      svg("circle", { r: 36, class: "node-halo" }),
      svg("circle", { r: 28, class: "node-body" }),
    );
    const glyph = svg("text", { y: 4, class: "node-glyph" });
    glyph.textContent = typeGlyph(a.type);
    const label = svg("text", { y: 48, class: "node-label" });
    label.textContent =
      a.name.length > 28
        ? a.name.slice(0, 12) + "…" + a.name.slice(-12)
        : a.name;
    const type = svg("text", { y: 61, class: "node-type" });
    type.textContent = a.type;
    const title = svg("title");
    title.textContent =
      a.name + " · " + a.type + " · " + a.connectivity + " · " + health(a) +
      " · " + number(callsFor(a.key)) + " observed calls · " + callPeriod();
    node.append(
      glyph,
      svg("circle", { r: 4, cx: 22, cy: 22, class: "health-marker" }),
      label,
      type,
      title,
    );
    node.onclick = () => {
      if (!dragMoved) pick(a.key);
    };
    node.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        pick(a.key);
      }
    };
    attachDrag(node, a.key);
    nodeLayer.append(node);
    graphNodes.set(a.key, node);
  }
  const endpointAgents = new Map(allAgents().map((a) => [nodeKey(a), a]));
  for (const link of links) {
    const isSelected = activeEdge && edgeKey(link) === state.selectedEdge;
    const highlight =
      isSelected || (activeSelection &&
      (link.from === activeSelection || link.to === activeSelection));
    const sender = endpointAgents.get(link.from), receiver = endpointAgents.get(link.to);
    const group = svg("g", {
      class: "communication" + (isSelected ? " selected-edge" : ""),
      tabindex: "0", role: "button", "aria-pressed": String(Boolean(isSelected)),
      "aria-label": "Inspect communication from " + sender.name + " on " + sender.plaza +
        " to " + receiver.name + " on " + receiver.plaza,
      "data-edge-key": edgeKey(link),
    });
    const path = svg("path", {
      class:
        "interaction" +
        (link.crossPlaza ? " cross-plaza" : "") +
        (link.errors ? " error" : "") +
        (highlight ? " highlight" : hasSelection ? " muted-edge" : ""),
      "marker-end": "url(#arrow)",
      "data-source": link.from,
      "data-target": link.to,
    });
    const title = svg("title");
    title.textContent =
      `${link.calls} observed calls · ${link.errors} failures` +
      (link.mean_duration_ms != null
        ? " · " + number(link.mean_duration_ms) + " ms mean"
        : "") +
      (link.last_seen ? " · last observed " + time(link.last_seen) : "");
    path.append(title);
    const label = svg("text", {
      class:
        "edge-label" +
        (highlight
          ? " highlight"
          : hasSelection || agents.length > 35
            ? " muted-edge"
            : ""),
    });
    const hit = svg("path", { class: "interaction-hit" });
    label.textContent = link.calls + " calls";
    group.append(hit, path, label);
    group.onpointerdown = (e) => { e.stopPropagation(); dragMoved = false; };
    group.onclick = (e) => { e.stopPropagation(); pickEdge(link); };
    group.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        pickEdge(link);
        [...world.querySelectorAll(".communication")].find((n) => n.getAttribute("data-edge-key") === edgeKey(link))?.focus({ preventScroll: true });
      }
    };
    edgeLayer.append(group);
    graphEdges.push({ key: edgeKey(link), from: link.from, to: link.to, path, hit, label, group });
  }
  $("#neighbors-only").disabled = !activeSelection;
  for (const id of ["focus-selection", "clear-selection"])
    $("#" + id).disabled = !hasSelection;
  $("#neighbors-only").setAttribute(
    "aria-pressed",
    String(state.neighborsOnly),
  );
  $("#graph-status").textContent =
    `${agents.length} agents · ${plazas.length} Plazas · ${links.length} relationships` +
    (activeEdge ? " · communication selected" : activeSelection
      ? ` · ${adjacent.size} neighbor${adjacent.size === 1 ? "" : "s"}`
      : "");
  updateGraphPositions();
  if (focusedEdge) [...world.querySelectorAll(".communication")]
    .find((n) => n.getAttribute("data-edge-key") === focusedEdge)?.focus({ preventScroll: true });
  if (changed) fitGraph();
  animateGraph();
  animateCalls();
}
function point(event) {
  const p = $("#topology").createSVGPoint();
  p.x = event.clientX;
  p.y = event.clientY;
  return p.matrixTransform($("#topology").getScreenCTM().inverse());
}
function endDrag() {
  dragging = null;
  if (deferredGraphRender) {
    deferredGraphRender = false;
    setTimeout(renderGraph, 0);
  }
}
function attachDrag(node, id) {
  node.onpointerdown = (e) => {
    e.stopPropagation();
    if (e.button !== 0) return;
    dragMoved = false;
    dragging = { id, p: point(e), start: { ...state.positions.get(id) } };
    node.setPointerCapture(e.pointerId);
  };
  node.onpointermove = (e) => {
    if (!dragging || dragging.id !== id) return;
    const p = point(e),
      dx = (p.x - dragging.p.x) / state.zoom,
      dy = (p.y - dragging.p.y) / state.zoom;
    if (Math.abs(dx) + Math.abs(dy) > 4) dragMoved = true;
    if (!dragMoved) return;
    Object.assign(state.positions.get(id), {
      x: dragging.start.x + dx,
      y: dragging.start.y + dy,
      fixed: true,
      pinned: true,
      vx: 0,
      vy: 0,
    });
    layoutModel.alpha = 0.22;
    updateGraphPositions();
    animateGraph();
  };
  node.onpointerup = endDrag;
  node.onpointercancel = endDrag;
}
$("#topology").onpointerdown = (e) => {
  if (e.target.closest(".node,.plaza-node,.communication") || e.button !== 0) return;
  dragMoved = false;
  dragging = { id: "pan", p: point(e), start: { x: state.x, y: state.y } };
  $("#topology").setPointerCapture(e.pointerId);
};
$("#topology").onpointermove = (e) => {
  if (dragging?.id !== "pan") return;
  const p = point(e),
    dx = p.x - dragging.p.x,
    dy = p.y - dragging.p.y;
  if (Math.abs(dx) + Math.abs(dy) > 4) dragMoved = true;
  state.x = dragging.start.x + dx;
  state.y = dragging.start.y + dy;
  graphTransform();
};
$("#topology").onpointerup = endDrag;
$("#topology").onpointercancel = endDrag;
$("#topology").onclick = (e) => {
  if (!dragMoved && !e.target.closest(".node,.plaza-node,.communication"))
    pick("");
};
function zoomGraph(factor, center) {
  const view = graphViewport(),
    p = center || { x: view.width / 2, y: view.height / 2 };
  const next = Math.max(0.00001, Math.min(4, state.zoom * factor)),
    ratio = next / state.zoom;
  state.x = p.x - (p.x - state.x) * ratio;
  state.y = p.y - (p.y - state.y) * ratio;
  state.zoom = next;
  graphTransform();
}
$("#topology").onwheel = (e) => {
  e.preventDefault();
  zoomGraph(e.deltaY > 0 ? 0.9 : 1.1, point(e));
};
$("#zoom-in").onclick = () => zoomGraph(1.2);
$("#zoom-out").onclick = () => zoomGraph(1 / 1.2);
$("#fit").onclick = () => fitGraph();
$("#fit-all").onclick = fitEntireGraph;
$("#relayout").onclick = () => {
  state.positions.clear();
  layoutModel = null;
  renderGraph();
};
function restoreDensity(value) {
  state.density = Number.isInteger(value) && value >= 0 && value <= 100 ? value : 50;
  $("#density").value = state.density;
  $("#density").setAttribute("aria-valuetext", state.density + "% density");
  $("#density-value").value = state.density + "%";
}
$("#density").oninput = () => {
  restoreDensity($("#density").valueAsNumber);
  if (layoutModel) {
    layoutModel.setDensity(state.density);
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) layoutModel.tick(90);
    updateGraphPositions();
    animateGraph();
  }
  preferences();
};
$("#focus-selection").onclick = () => {
  const edge = selectedEdge();
  if (edge) { fitGraph([edge.from, edge.to]); return; }
  const { adjacent } = graphContext(filtered());
  fitGraph([state.selected, ...adjacent]);
};
$("#neighbors-only").onclick = () => {
  state.neighborsOnly = !state.neighborsOnly;
  render();
  fitGraph();
};
$("#clear-selection").onclick = () => {
  pick("");
  renderSummary();
};
function maximizeGraph(enabled) {
  $("#topology-workspace").classList.toggle("maximized", enabled);
  document.body.classList.toggle("topology-maximized", enabled);
  $("#maximize").setAttribute("aria-pressed", String(enabled));
  $("#maximize").setAttribute(
    "aria-label",
    enabled ? "Restore topology" : "Maximize topology",
  );
  $("#maximize").title = enabled ? "Restore topology" : "Maximize topology";
  requestAnimationFrame(() => fitGraph());
}
$("#maximize").onclick = () =>
  maximizeGraph(!$("#topology-workspace").classList.contains("maximized"));
window.addEventListener("keydown", (e) => {
  if (
    e.key === "Escape" &&
    $("#topology-workspace").classList.contains("maximized") &&
    !$("dialog[open]")
  ) {
    e.preventDefault();
    maximizeGraph(false);
    $("#maximize").focus();
  }
});
new ResizeObserver(() => fitGraph()).observe($("#topology"));
function edgeActions(owner, job) {
  const jobId = job.log_job_id ?? job.id, traceId = job.trace_id;
  const actions = el("div", undefined, "edge-actions");
  if (traceId) {
    const trace = el("button", "View trace");
    trace.onclick = () => {
      pick(owner.key);
      state.trace = traceId;
      state.tab = "traces";
      renderInspector(true);
    };
    actions.append(trace);
  }
  if (owner.logs_allowed && (jobId || traceId)) {
    const logs = el("button", "View logs");
    logs.onclick = () => jobLogs.open(owner, job);
    actions.append(logs);
  }
  return actions;
}
function renderEdgeInspector(edge, force = false) {
  const details = edgeDetails(state.data, edge);
  if (!details) return;
  const { source, target } = details;
  const scope = "edge:" + edgeKey(edge);
  if (!force && source.logs_allowed && target.logs_allowed && state.tab === "jobs" && jobsPanel.mounted(scope)) {
    jobsPanel.refresh();
    return;
  }
  jobsPanel.cancel();
  const container = $("#inspector");
  container.className = "inspector";
  container.dataset.agent = "edge:" + edgeKey(edge);
  container.dataset.tab = state.tab;
  container.replaceChildren();
  const heading = el("div", undefined, "agent-heading");
  heading.append(el("div", "Communication", "overline"),
    el("h2", source.name + " → " + target.name),
    el("div", source.plaza + (source.source === target.source ? "" : " → " + target.plaza), "identity"));
  const badges = el("div", undefined, "badges");
  badges.append(badge(edge.crossPlaza ? "Across Plazas" : "Within Plaza"), badge(callPeriod()));
  const endpoints = el("div", undefined, "edge-actions");
  for (const [role, owner] of [["Sender", source], ["Receiver", target]]) {
    const button = el("button", role);
    button.setAttribute("aria-label", "Inspect " + role.toLowerCase() + ": " + owner.name);
    button.onclick = () => pick(owner.key);
    endpoints.append(button);
  }
  heading.append(badges, endpoints);
  container.append(heading);
  const nav = el("nav", undefined, "tabs");
  nav.setAttribute("aria-label", "Communication details");
  if (!["metrics", "jobs"].includes(state.tab)) state.tab = "metrics";
  for (const id of ["metrics", "jobs"]) {
    const button = el("button", id === "metrics" ? "Metrics" : "Jobs", state.tab === id ? "active" : "");
    button.setAttribute("aria-pressed", String(state.tab === id));
    button.onclick = () => { state.tab = id; renderEdgeInspector(selectedEdge(), true); };
    nav.append(button);
  }
  container.append(nav);
  const body = el("div", undefined, "detail-body");
  container.append(body);
  if (source.telemetry_stale || target.telemetry_stale)
    body.append(el("p", "Endpoint telemetry is stale; current jobs may have changed.", "stale-note"));
  if (state.tab === "metrics") {
    const rows = [
      ["Observed calls", number(edge.calls)],
      ["Failures", number(edge.errors)],
      ["Failure rate", edge.calls ? number(100 * edge.errors / edge.calls) + "%" : "—"],
      ["Mean duration", edge.mean_duration_ms == null ? "Unavailable" : number(edge.mean_duration_ms) + " ms"],
      ["First observed", edge.first_seen ? new Date(edge.first_seen * 1000).toLocaleString() : "—"],
      ["Last observed", edge.last_seen ? new Date(edge.last_seen * 1000).toLocaleString() : "—"],
    ];
    if (state.data.call_window_sec) rows.splice(3, 0, ["Calls / minute", number(edge.calls * 60 / state.data.call_window_sec)]);
    for (const [label, value] of rows) {
      const row = el("div", undefined, "metric-row edge-metric");
      row.append(el("span", label), el("strong", value));
      body.append(row);
    }
    body.append(el("p", "Completed outbound calls · " + callPeriod(), "status-note"));
    return;
  }
  jobsPanel.mount(body, source.key === target.key ? [source] : [source, target], scope, { source, target });
}
function renderInspector(force = false) {
  if (state.selectedEdge) {
    const edge = selectedEdge();
    if (edge) { renderEdgeInspector(edge, force); return; }
    state.selectedEdge = "";
  }
  const a = selected(),
    container = $("#inspector");
  if (!a) {
    jobsPanel.cancel();
    if (state.selected) {
      container.replaceChildren(
        el(
          "div",
          "The selected agent is no longer visible. Trust or permissions may have changed.",
          "empty",
        ),
      );
      state.selected = "";
      state.tail = false;
      state.logs = [];
      state.logRequest = null;
    } else if (force || container.dataset.agent) {
      const empty = el("div", undefined, "inspector-empty");
      empty.append(
        el("h2", "Select a node or edge"),
        el(
          "p",
          state.data
            ? "Select a node or communication edge to inspect it."
            : "Sign in to inspect monitored agents.",
        ),
      );
      container.replaceChildren(empty);
    }
    delete container.dataset.agent;
    delete container.dataset.tab;
    return;
  }
  if (!a.logs_allowed) {
    state.logs = [];
    state.logRequest = null;
    state.tail = false;
  }
  if (!force && a.logs_allowed && state.tab === "jobs" && jobsPanel.mounted(a.key)) {
    jobsPanel.refresh();
    const runtime = $("#jobs-runtime");
    if (runtime) runtime.textContent = "Runtime " + a.observability.version + " · uptime " + ago(a.observability.uptime_sec).replace(" ago", "");
    return;
  }
  jobsPanel.cancel();
  if (
    !force &&
    a.logs_allowed &&
    state.tab === "logs" &&
    container.dataset.agent === a.key &&
    container.dataset.tab === "logs"
  )
    return;
  container.dataset.agent = a.key;
  container.dataset.tab = state.tab;
  container.replaceChildren();
  const heading = el("div", undefined, "agent-heading");
  heading.append(
    el("div", a.plaza + " / " + a.type, "section-label"),
    el("h2", a.name),
    el("div", a.id, "identity"),
  );
  const badges = el("div", undefined, "badges");
  badges.append(
    badge(a.connectivity, a.connectivity === "online" ? "healthy" : "unknown"),
    badge(health(a), health(a)),
    badge(a.observability.activity || "unknown"),
  );
  heading.append(badges);
  const tabs = el("nav", undefined, "tabs");
  tabs.setAttribute("aria-label", "Agent details");
  const panels = (a.observability.panels || []).filter(
    (p) => p.type === "metric_groups" && p.id && p.label,
  );
  const entries = [
    ...(a.isPlaza ? [{ id: "connection", label: "Connections" }] : []),
    { id: "jobs", label: "Jobs" },
    { id: "metrics", label: "Metrics" },
    ...panels,
    { id: "traces", label: "Traces" },
    { id: "logs", label: "Logs" },
  ];
  if (!entries.some((p) => p.id === state.tab)) state.tab = "jobs";
  for (const entry of entries) {
    const tab = entry.id;
    const b = el("button", entry.label, state.tab === tab ? "active" : "");
    b.setAttribute("aria-pressed", String(state.tab === tab));
    b.onclick = () => {
      state.tab = tab;
      state.tail = false;
      preferences();
      renderInspector(true);
    };
    tabs.append(b);
  }
  const body = el("div", undefined, "detail-body");
  container.append(heading, tabs, body);
  if (state.tab === "connection") {
    renderConnections(body, a);
    return;
  }
  if (a.telemetry_stale)
    body.append(
      el(
        "p",
        "Last-known telemetry · " + ago(a.telemetry_age_sec),
        "stale-note",
      ),
    );
  if (!a.telemetry_supported) {
    body.append(
      el(
        "p",
        a.isPlaza ? "No authorized runtime telemetry is available for this Plaza."
          : a.registration_state === "not_registered"
          ? "Trust approved; this agent has not connected to Plaza yet."
          : "This agent has not reported observability data. Upgrade its runtime to enable metrics and diagnostics.",
        "muted",
      ),
    );
    return;
  }
  if (state.tab === "jobs") renderJobs(body, a);
  if (state.tab === "metrics") renderMetrics(body, a);
  const panel = panels.find((p) => p.id === state.tab);
  if (panel) renderMetricGroups(body, a, panel);
  if (state.tab === "traces") renderTraces(body, a);
  if (state.tab === "logs") renderLogs(body, a);
}
function renderConnections(body, a) {
  const plaza = a.connection;
  const members = allAgents().filter((agent) => agent.source === a.source && nodeKey(agent) !== a.key);
  const edges = topologyInteractions(state.data, new Set([
    ...allAgents().map(nodeKey), ...(state.data?.plazas || []).map((p) => plazaKey(p.id)),
  ])).filter((edge) => edge.from === a.key || edge.to === a.key);
  for (const [label, value] of [
    ["Monitoring connection", plaza.status], ["Endpoint", a.endpoint],
    ["Authorized agents", members.length],
    ["Online agents", members.filter((agent) => agent.connectivity === "online").length],
    ["Observed calls", edges.reduce((sum, edge) => sum + edge.calls, 0)],
    ["Observed failures", edges.reduce((sum, edge) => sum + edge.errors, 0)],
    ["Snapshot", plaza.generated_at ? time(plaza.generated_at) : "Unavailable"],
  ]) {
    const row = el("div", undefined, "metric-row connection-row");
    row.append(el("span", label), el("span", String(value)));
    body.append(row);
  }
  if (plaza.error) body.append(el("p", plaza.error, "stale-note"));
  body.append(el("p", "Registration links show connected members; arrows show observed calls.", "status-note"));
  for (const member of members) {
    const button = el("button", member.name + " · " + member.connectivity, "connection-agent");
    button.onclick = () => pick(member.key);
    body.append(button);
  }
}
function renderJobs(body, a) {
  const obs = a.observability;
  if (!a.logs_allowed) {
    body.append(el("p", "Detailed job history requires the agent owner or Plaza administrator.", "status-note"));
    for (const job of obs.jobs || []) body.append(el("div", [job.id, job.status, job.kind].filter(Boolean).join(" · "), "job"));
    return;
  }
  jobsPanel.mount(body, [a], a.key);
  const runtime = el("p", "Runtime " + obs.version + " · uptime " + ago(obs.uptime_sec).replace(" ago", ""), "status-note");
  runtime.id = "jobs-runtime";
  body.append(runtime);
}
function renderMetrics(body, a) {
  const prefixes = (a.observability.panels || [])
    .map((p) => p.prefix)
    .filter(Boolean);
  const metrics = (a.observability.metrics || []).filter(
    (m) => !prefixes.some((p) => m.name.startsWith(p)),
  );
  if (!metrics.length)
    body.append(el("div", "No custom metrics reported.", "empty"));
  for (const m of metrics) {
    const row = el("div", undefined, "metric-row");
    const labels = Object.entries(m.labels || {})
      .map(([k, v]) => k + "=" + v)
      .join(", ");
    row.append(
      el("span", m.name + (labels ? " · " + labels : "")),
      el("b", number(m.value) + " " + (m.unit === "count" ? "" : m.unit)),
    );
    row.title = m.kind || "gauge";
    body.append(row);
  }
}
function renderMetricGroups(body, a, panel) {
  const groups = new Map();
  for (const m of a.observability.metrics || []) {
    if (!m.name.startsWith(panel.prefix || "")) continue;
    const name = m.labels?.[panel.group_by] || "default";
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(m);
  }
  if (!groups.size)
    body.append(el("div", "No executions observed yet.", "empty"));
  for (const [name, metrics] of groups) {
    const get = (metric, filter = {}) =>
      metrics
        .filter(
          (m) =>
            m.name === metric &&
            Object.entries(filter).every(([k, v]) => m.labels?.[k] === v),
        )
        .reduce((n, m) => n + Number(m.value || 0), 0);
    const block = el("div", undefined, "metric-group");
    block.append(el("strong", name));
    const stats = el("div", undefined, "group-stats");
    for (const measure of panel.measures || []) {
      let value = get(measure.metric, measure.filter);
      if (measure.divide_by) {
        const count = get(measure.divide_by);
        value = count ? value / count : null;
      }
      const s = el("span", measure.label + " ");
      s.append(
        el(
          "b",
          value == null
            ? "—"
            : number(value) + (measure.unit ? " " + measure.unit : ""),
        ),
      );
      stats.append(s);
    }
    block.append(stats);
    const last = metrics.find((m) => m.name === panel.timestamp_metric);
    if (last)
      block.append(el("div", "Last success " + time(last.value), "job-meta"));
    const stages = metrics.filter((m) => m.name === panel.stage_metric);
    if (stages.length)
      block.append(
        el(
          "div",
          stages
            .map((m) => m.labels.stage + " " + number(m.value) + " " + m.unit)
            .join(" · "),
          "job-meta",
        ),
      );
    body.append(block);
  }
  if (panel.notes) body.append(el("p", panel.notes, "status-note"));
}
function renderTraces(body, a) {
  const plaza = state.data.plazas.find((p) => p.id === a.source);
  const allEvents = (state.data?.plazas || []).flatMap((p) =>
    p.events.map((e) => ({ ...e, source: p.id, plazaName: p.name })),
  );
  const relevant = allEvents.filter(
    (e) =>
      (e.agent_id === a.id && e.source === a.source) ||
      (e.peer_agent_id === a.id && (e.peer_source || e.source) === a.source),
  );
  const ids = [...new Set(relevant.map((e) => e.trace_id).filter(Boolean))];
  if (!ids.length) {
    body.append(el("div", "No recent trace events.", "empty"));
    return;
  }
  if (state.trace && ids.includes(state.trace)) {
    const back = el("button", "← All traces");
    back.onclick = () => {
      state.trace = "";
      renderInspector(true);
    };
    body.append(back, el("p", state.trace, "mono"));
    const events = allEvents
      .filter((e) => e.trace_id === state.trace)
      .sort((a, b) => a.timestamp - b.timestamp);
    for (const e of events) {
      const row = el(
        "div",
        undefined,
        "trace-event" +
          (["error", "failed", "denied"].includes(e.status) ? " error" : ""),
      );
      const agent = state.data.plazas
        .find((p) => p.id === e.source)
        ?.agents.find((v) => v.id === e.agent_id);
      row.append(
        el(
          "span",
          (agent?.name || e.agent_id) +
            " / " +
            e.plazaName +
            " · " +
            (e.operation || e.event),
        ),
        el(
          "small",
          time(e.timestamp) +
            " · " +
            e.event +
            (e.status ? " · " + e.status : "") +
            (e.duration_ms != null
              ? " · " + number(e.duration_ms) + " ms"
              : ""),
        ),
      );
      if (e.span_id)
        row.title =
          "Span " + e.span_id + " / parent " + (e.parent_span_id || "root");
      body.append(row);
    }
    body.append(
      el(
        "p",
        "Observed events; missing agents may leave gaps in this trace.",
        "status-note",
      ),
    );
    return;
  }
  for (const id of ids.slice(0, 30)) {
    const events = relevant.filter((e) => e.trace_id === id);
    const e = events[0];
    const b = el("button", undefined, "trace-button");
    b.append(
      el("span", (e.operation || "Interaction") + " · " + time(e.timestamp)),
      el("small", id + " · " + events.length + " events"),
    );
    b.onclick = () => {
      state.trace = id;
      renderInspector(true);
    };
    body.append(b);
  }
}
function renderLogs(body, a) {
  if (!a.logs_allowed) {
    body.append(
      el(
        "div",
        "Diagnostic logs require the agent owner or Plaza administrator account.",
        "empty",
      ),
    );
    return;
  }
  const filters = el("div", undefined, "log-filters");
  const level = el("select");
  level.id = "log-level";
  level.setAttribute("aria-label", "Log severity");
  for (const value of ["", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]) {
    const o = el("option", value || "All severities");
    o.value = value;
    level.append(o);
  }
  const job = el("input");
  job.id = "log-job";
  job.placeholder = "Job ID";
  job.setAttribute("aria-label", "Log job ID");
  const trace = el("input");
  trace.id = "log-trace";
  trace.placeholder = "Trace ID";
  trace.value = state.trace;
  trace.setAttribute("aria-label", "Log trace ID");
  const since = el("input");
  since.id = "log-since";
  since.type = "datetime-local";
  since.setAttribute("aria-label", "Logs since");
  filters.append(level, job, trace, since);
  const actions = el("div", undefined, "log-actions");
  const fetch = el("button", "Retrieve logs");
  fetch.id = "retrieve-logs";
  fetch.onclick = () => requestLogs(false);
  const tailLabel = el("label");
  const tail = el("input");
  tail.type = "checkbox";
  tail.id = "tail-logs";
  tail.checked = state.tail;
  tail.onchange = () => {
    state.tail = tail.checked;
    if (state.tail) requestLogs(false);
  };
  tailLabel.append(tail, document.createTextNode("Live tail"));
  const exportButton = el("button", "Export");
  exportButton.onclick = () => {
    const blob = new Blob(
        [state.logs.map((v) => JSON.stringify(v)).join("\n")],
        { type: "application/x-ndjson" },
      ),
      url = URL.createObjectURL(blob),
      link = el("a");
    link.href = url;
    link.download = "agent-diagnostics.ndjson";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  actions.append(fetch, tailLabel, exportButton);
  const status = el(
    "p",
    state.logRequest
      ? "Waiting for agent heartbeat…"
      : "Retrieve detailed logs from this monitored agent.",
    "status-note",
  );
  status.id = "log-status";
  const logs = el("div", undefined, "logs");
  logs.id = "logs";
  const older = el("button", "Load earlier");
  older.id = "older-logs";
  older.hidden = !state.logs.length;
  older.onclick = () => requestLogs(true);
  body.append(filters, actions, status, logs, older);
  drawLogs();
}
function drawLogs() {
  const container = $("#logs");
  if (!container) return;
  renderLogEntries(container, state.logs);
  if ($("#older-logs")) $("#older-logs").hidden = !state.logs.length;
}
async function requestLogs(older = false) {
  const a = selected();
  if (!a || state.logRequest || !$("#log-level")) return;
  const query = {
    limit: 100,
    level: $("#log-level").value,
    job_id: $("#log-job").value,
    trace_id: $("#log-trace").value,
  };
  if ($("#log-since").value)
    query.since = new Date($("#log-since").value).getTime() / 1000;
  if (older && state.logs.length)
    query.before = Math.min(...state.logs.map((l) => l.cursor));
  else if (state.tail && state.logs.length)
    query.after = Math.max(...state.logs.map((l) => l.cursor));
  try {
    $("#retrieve-logs").disabled = true;
    const result = await api(
      "/plazas/" +
        encodeURIComponent(a.source) +
        "/agents/" +
        encodeURIComponent(a.id) +
        "/logs",
      query,
    );
    state.logRequest = {
      id: result.id,
      source: a.source,
      key: a.key,
      started: Date.now(),
      append: older || state.tail,
    };
    $("#log-status").textContent = "Waiting for agent heartbeat…";
  } catch (e) {
    if ($("#log-status")) $("#log-status").textContent = e.message;
    if ($("#retrieve-logs")) $("#retrieve-logs").disabled = false;
  }
}
async function pollLogs() {
  const request = state.logRequest;
  if (!request) return;
  try {
    const value = await api(
      "/plazas/" +
        encodeURIComponent(request.source) +
        "/diagnostics/" +
        encodeURIComponent(request.id),
    );
    if (state.logRequest !== request) return;
    if (value.status === "ready") {
      if (state.selected === request.key) {
        const items = value.result.items || [];
        state.logs = request.append
          ? [
              ...new Map(
                [...state.logs, ...items].map((v) => [v.cursor, v]),
              ).values(),
            ].slice(-1000)
          : items;
        drawLogs();
        if ($("#log-status"))
          $("#log-status").textContent =
            items.length +
            " entries retrieved · " +
            (value.result.cached
              ? "Cached " + time(value.result.retrieved_at)
              : time(Date.now() / 1000));
      }
      state.logRequest = null;
      if ($("#retrieve-logs")) $("#retrieve-logs").disabled = false;
    } else if (Date.now() - request.started > 120000)
      throw Error("Log request expired. The agent may be offline.");
  } catch (e) {
    if (state.logRequest !== request) return;
    state.logRequest = null;
    if ($("#log-status")) $("#log-status").textContent = e.message;
    if ($("#retrieve-logs")) $("#retrieve-logs").disabled = false;
  }
}
async function connections() {
  const dialog = $("#connection-dialog");
  const accountToken = session().access_token;
  try {
    const value = await api("/sources");
    if (accountToken !== session().access_token) return;
    const list = $("#connection-list");
    list.replaceChildren();
    for (const source of value.sources.filter((v) => !v.local)) {
      const row = el("div", undefined, "connection");
      const heading = el("div", undefined, "connection-header");
      heading.append(
        el("strong", source.name),
        badge(
          source.connected ? "Connected" : "Disconnected",
          source.connected ? "healthy" : "unknown",
        ),
      );
      const url = el("div", source.url, "connection-url"),
        actions = el("div", undefined, "connection-actions"),
        status = el("p", "", "connection-status");
      const connect = el("button", source.connected ? "Reconnect" : "Connect");
      connect.onclick = async () => {
        try {
          connect.disabled = true;
          const result = await api("/sources/" + source.id + "/connect", {});
          const link = el("a", "Open Plaza approval ↗", "button");
          link.href = result.approval_url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          const check = el("button", "Check approval");
          check.onclick = async () => {
            try {
              const r = await api("/sources/" + source.id + "/poll", {});
              status.textContent =
                r.status === "connected"
                  ? "Connected."
                  : "Waiting for approval.";
              if (r.status === "connected") {
                await load();
                connections();
              }
            } catch (e) {
              status.textContent = e.message;
            }
          };
          actions.append(link, check);
          status.textContent =
            "Open the remote Plaza and approve monitoring, then check approval.";
        } catch (e) {
          status.textContent = e.message;
        } finally {
          connect.disabled = false;
        }
      };
      const disconnect = el("button", "Disconnect");
      disconnect.disabled = !source.connected;
      disconnect.onclick = async () => {
        try {
          await api("/sources/" + source.id + "/connect", null, "DELETE");
          await load();
          connections();
        } catch (e) {
          status.textContent = e.message;
        }
      };
      actions.append(connect, disconnect);
      if (source.removable) {
        const remove = el("button", "Remove");
        remove.setAttribute("aria-label", "Remove " + source.name);
        remove.onclick = async () => {
          const accountToken = session().access_token;
          try {
            remove.disabled = true;
            await api("/sources/" + source.id, null, "DELETE");
            if (accountToken !== session().access_token) return;
            state.sourceRevision++;
            if (state.data) state.data.plazas = state.data.plazas.filter((p) => p.id !== source.id);
            if (state.source === source.id) {
              state.source = "all";
              preferences();
            }
            render();
            await connections();
            await reloadSources();
          } catch (e) {
            status.textContent = e.message;
            remove.disabled = false;
          }
        };
        actions.append(remove);
      }
      row.append(heading, url, actions, status);
      list.append(row);
    }
    if (!list.children.length)
      list.append(
        el(
          "p",
          "Add a Plaza URL above to start monitoring.",
          "muted",
        ),
      );
    if (!dialog.open) dialog.showModal();
  } catch (e) {
    notice(e.message);
  }
}
$("#add-plaza-form").onsubmit = async (e) => {
  e.preventDefault();
  const accountToken = session().access_token;
  const button = $("#add-plaza"), status = $("#add-plaza-status");
  button.disabled = true;
  status.textContent = "Adding Plaza…";
  try {
    const source = await api("/sources", { url: $("#plaza-url").value, name: $("#plaza-name").value });
    if (accountToken !== session().access_token) return;
    state.sourceRevision++;
    if (state.data && !state.data.plazas.some((p) => p.id === source.id)) {
      state.data.plazas.push({ id: source.id, name: source.name, url: source.url,
        status: "disconnected", error: "Connect this Plaza to monitor its agents.",
        agents: [], events: [], links: [], relationships: [], plaza_agent_id: null });
      render();
    }
    $("#add-plaza-form").reset();
    status.textContent = "Plaza added. Connect to approve monitoring.";
    await connections();
    await reloadSources();
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
};
for (const field of [$("#plaza-url"), $("#plaza-name")])
  field.oninput = () => { $("#add-plaza-status").textContent = ""; };
$("#connections").onclick = () => {
  $("#add-plaza-status").textContent = "";
  connections();
};
$("#help").onclick = () => $("#help-dialog").showModal();
for (const b of document.querySelectorAll("[data-close]"))
  b.onclick = () => b.closest("dialog").close();
$("#refresh").onclick = load;
$("#check-login").onclick = load;
$("#search").oninput = () => {
  state.search = $("#search").value;
  filtersChanged();
};
document.addEventListener("pointerdown", (e) => {
  if (!$("#pit-type").contains(e.target)) $("#pit-type").open = false;
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && $("#pit-type").open) {
    e.preventDefault();
    e.stopPropagation();
    $("#pit-type").open = false;
    $("#pit-type summary").focus();
  }
});
$("#health").onchange = () => {
  state.health = $("#health").value;
  filtersChanged();
};
function restoreCallRange(prefs) {
  for (const [field, id] of [["minCalls", "min-calls"], ["maxCalls", "max-calls"]]) {
    const value = prefs[field];
    state[field] = Number.isSafeInteger(value) && value >= 0 ? value : null;
    $("#" + id).value = state[field] ?? "";
    $("#" + id).setCustomValidity("");
    $("#" + id).removeAttribute("aria-invalid");
  }
  $("#call-filter-error").textContent = "";
}
function callRangeChanged() {
  const inputs = [$("#min-calls"), $("#max-calls")];
  const values = inputs.map((input) => input.value === "" && !input.validity.badInput ? null : input.valueAsNumber);
  const invalid = values.some((value) => value != null && (!Number.isSafeInteger(value) || value < 0));
  const reversed = values[0] != null && values[1] != null && values[0] > values[1];
  const error = invalid ? "Calls must be nonnegative whole numbers within the supported range."
    : reversed ? "Min calls must not exceed max calls." : "";
  for (const input of inputs) {
    input.setCustomValidity(error);
    input.setAttribute("aria-invalid", String(!!error));
  }
  $("#call-filter-error").textContent = error;
  if (error) return; // Invalid drafts cannot replace a valid saved view.
  [state.minCalls, state.maxCalls] = values;
  filtersChanged();
}
$("#min-calls").oninput = callRangeChanged;
$("#max-calls").oninput = callRangeChanged;
function restoreCallWindow(prefs) {
  const minutes = prefs.callWindowMinutes;
  state.callWindowMinutes = Number.isInteger(minutes) && minutes >= 1 && minutes <= 10080 ? minutes : null;
  state.callWindowUnit = prefs.callWindowUnit === "hours" && (minutes == null || minutes % 60 === 0) ? "hours" : "minutes";
  $("#call-window").value = state.callWindowMinutes == null ? "" : state.callWindowMinutes / (state.callWindowUnit === "hours" ? 60 : 1);
  $("#call-window").max = state.callWindowUnit === "hours" ? "168" : "10080";
  $("#call-window-unit").value = state.callWindowUnit;
  $("#call-window").setCustomValidity("");
  $("#call-window").removeAttribute("aria-invalid");
  $("#call-window-error").textContent = "";
  $("#call-window-status").textContent = "";
}
function refreshCallWindow() {
  $("#call-window-status").textContent = "Updating calls…";
  // Wait for any older snapshot, then request the current window. Its response
  // guard prevents old time-range results from replacing a newer selection.
  reloadSources();
}
function callWindowChanged() {
  const input = $("#call-window"), unit = $("#call-window-unit").value;
  const value = input.value === "" && !input.validity.badInput ? null : input.valueAsNumber;
  const factor = unit === "hours" ? 60 : 1, max = 10080 / factor;
  input.max = String(max);
  const error = value != null && (!Number.isInteger(value) || value < 1 || value > max)
    ? "Past must be a whole number from 1 to " + max + " " + unit + "." : "";
  input.setCustomValidity(error);
  input.setAttribute("aria-invalid", String(!!error));
  $("#call-window-error").textContent = error;
  if (error) return;
  const minutes = value == null ? null : value * factor;
  const changed = minutes !== state.callWindowMinutes;
  state.callWindowMinutes = minutes;
  state.callWindowUnit = unit;
  preferences();
  if (changed) refreshCallWindow();
}
$("#call-window").oninput = callWindowChanged;
$("#call-window-unit").onchange = callWindowChanged;
const approvalCode = new URLSearchParams(location.search).get("approve");
for (const link of document.querySelectorAll("[data-plaza-signin]")) {
  const params = new URLSearchParams({
    signin: "1",
    next: location.pathname + location.search,
  });
  link.href = "/plazas?" + params;
}
if (approvalCode) {
  $("#approval").hidden = false;
  $("#approval-code").textContent = approvalCode;
  for (const [id, approved] of [
    ["approve", true],
    ["deny", false],
  ])
    $("#" + id).onclick = async () => {
      try {
        await api("/device/approve", { code: approvalCode, approved });
        $("#approval").replaceChildren(
          el(
            "strong",
            approved
              ? "Monitoring approved. Return to the originating dashboard and check approval."
              : "Monitoring denied.",
          ),
        );
      } catch (e) {
        notice(e.message);
      }
    };
}
window.addEventListener("storage", (e) => {
  if (e.key === "plaza.ui.authSession") { jobLogs.revalidate(); reloadSources(); }
});
try {
  const prefs = await api("/preferences");
  for (const k of ["source", "health", "search", "tab"])
    if (typeof prefs[k] === "string") state[k] = prefs[k];
  state.types = Array.isArray(prefs.types) ? [...new Set(prefs.types.filter((type) => typeof type === "string" && type))]
    : typeof prefs.type === "string" && prefs.type !== "all" ? [prefs.type] : [];
  if (!/^[a-z0-9_-]{1,60}$/.test(state.tab)) state.tab = "jobs";
  restoreCallRange(prefs);
  restoreCallWindow(prefs);
  restoreDensity(prefs.density);
  state.jobFilters = jobFilters(prefs.jobFilters);
  $("#search").value = state.search;
  $("#health").value = state.health;
} catch {}
await load();
setInterval(() => {
  if (!document.hidden) load();
}, 10000);
setInterval(() => {
  if (!document.hidden) pollLogs();
}, 2000);
setInterval(() => {
  if (
    !document.hidden &&
    state.tab === "logs" &&
    state.tail &&
    !state.logRequest
  )
    requestLogs(false);
}, 10000);
