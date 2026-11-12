/** Server-backed job lists with stable controls during live refreshes. */
export const defaultJobFilters = () => ({ state: "all", activity: "work", status: "all", search: "", minutes: null });
export function isRequest(job) {
  return ["http", "outbound_http", "request", "http_request"].includes(String(job.kind || "").toLowerCase()) ||
    !job.kind && /^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+\//.test(job.label || "");
}
export function jobFilters(value = {}) {
  const result = defaultJobFilters();
  for (const [key, allowed] of Object.entries({ state: ["all", "active", "finished"], activity: ["work", "requests", "all"], status: ["all", "succeeded", "failed", "cancelled", "expired"] }))
    if (allowed.includes(value[key])) result[key] = value[key];
  if (typeof value.search === "string") result.search = value.search.slice(0, 160);
  if (Number.isInteger(value.minutes) && value.minutes >= 1 && value.minutes <= 10080) result.minutes = value.minutes;
  return result;
}
function el(tag, text, cls) {
  const n = document.createElement(tag);
  if (text != null) n.textContent = text;
  if (cls) n.className = cls;
  return n;
}
function date(value) {
  if (!value) return "—";
  const parsed = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(+parsed) ? "—" : parsed.toLocaleString();
}
export class JobsPanel {
  constructor({ api, filters, changed, actions, token }) {
    Object.assign(this, { api, filters, changed, actions, token });
    this.revision = 0;
    this.pending = new Set();
    this.results = new Map();
  }
  mounted(scope) { return this.scope === scope && this.body?.isConnected; }
  cancel() { this.revision++; this.scope = ""; clearTimeout(this.timer); }
  mount(body, owners, scope, peer = null) {
    this.cancel();
    Object.assign(this, { body, owners, scope, peer });
    this.results.clear();
    const f = this.filters(), controls = el("div", null, "job-filters");
    const search = el("input");
    search.type = "search"; search.placeholder = "Job name or ID…";
    search.maxLength = 160; search.value = f.search;
    search.setAttribute("aria-label", "Filter jobs by name or ID");
    search.className = "job-search";
    const update = () => {
      if (!minutes.checkValidity()) {
        this.revision++; clearTimeout(this.timer);
        this.status.textContent = "Finished range must be 1–10080 minutes, or empty.";
        return;
      }
      this.changed({ state: state.value, activity: activity.value, status: status.value, search: search.value, minutes: minutes.value ? minutes.valueAsNumber : null });
      this.revision++; this.results.clear();
      this.rows.replaceChildren(); this.status.textContent = "Updating jobs…"; this.more.hidden = true;
      clearTimeout(this.timer); this.timer = setTimeout(() => this.refresh(), 250);
    };
    const select = (label, key, values) => {
      const node = el("select"); node.setAttribute("aria-label", label);
      for (const [value, name] of values) { const o = el("option", name); o.value = value; node.append(o); }
      node.value = f[key]; node.onchange = update; return node;
    };
    const state = select("Job state", "state", [["all", "Active + finished"], ["active", "Active"], ["finished", "Finished"]]);
    const activity = select("Job activity", "activity", [["work", "Agent jobs"], ["requests", "HTTP requests"], ["all", "All activity"]]);
    const status = select("Job outcome", "status", [["all", "All outcomes"], ["succeeded", "Successful"], ["failed", "Failed"], ["cancelled", "Cancelled"], ["expired", "Expired"]]);
    const range = el("label", null, "job-range");
    const minutes = el("input"); minutes.type = "number"; minutes.min = 1; minutes.max = 10080;
    this.rangeInput = minutes;
    minutes.placeholder = "All"; minutes.value = f.minutes || "";
    minutes.setAttribute("aria-label", "Finished jobs past minutes"); minutes.oninput = update;
    range.append(el("span", "Past"), minutes, el("span", "min"));
    search.oninput = update;
    controls.append(search, state, activity, status, range);
    this.status = el("p", "Loading jobs…", "status-note job-list-status");
    this.status.setAttribute("role", "status");
    this.rows = el("div", null, "job-list-rows");
    this.more = el("button", "Load older finished jobs");
    this.more.className = "job-load-more"; this.more.hidden = true;
    this.more.onclick = () => this.refresh(true);
    const refresh = el("button", "Refresh jobs"); refresh.className = "job-refresh";
    refresh.onclick = () => { this.results.clear(); this.refresh(); };
    body.append(controls, this.status, this.rows, this.more, refresh);
    this.refresh();
  }
  async refresh(more = false) {
    if (!this.body?.isConnected || !this.scope || !this.rangeInput.checkValidity()) return;
    const scope = this.scope, revision = this.revision, token = this.token(), f = { ...this.filters() };
    const requestKey = JSON.stringify([scope, revision, token]);
    if (this.pending.has(requestKey)) return;
    this.pending.add(requestKey);
    this.more.disabled = true;
    const current = () => this.mounted(scope) && this.revision === revision && this.token() === token;
    try {
      const responses = await Promise.all(this.owners.map(async (owner) => {
        const old = this.results.get(owner.key);
        if (more && !old?.next_cursor) return [owner.key, old];
        const query = new URLSearchParams({ state: f.state, activity: f.activity, status: f.status, search: f.search, limit: "50" });
        if (f.minutes) query.set("since", String(Date.now() / 1000 - f.minutes * 60));
        if (more && old?.next_cursor) query.set("cursor", old.next_cursor);
        if (this.peer) {
          const peer = this.peer.source.key === owner.key ? this.peer.target : this.peer.source;
          query.set("peer_id", peer.id); query.set("peer_origin", peer.endpoint || "");
          query.set("direction", owner.key === this.peer.source.key ? "outbound" : "inbound");
          query.set("strict_peer", String(peer.source !== owner.source));
        }
        try {
          const value = await this.api("/plazas/" + encodeURIComponent(owner.source) + "/agents/" + encodeURIComponent(owner.id) + "/jobs?" + query);
          if (!Array.isArray(value.active) || !Array.isArray(value.finished)) throw Error("Invalid job response.");
          const merged = new Map([...(old?.finished || []), ...value.finished].map((j) => [j.key, j]));
          // Refresh keeps loaded older rows; filters always start a fresh list.
          const finished = [...merged.values()].filter((j) => j.ended_at >= Date.now() / 1000 - (f.minutes || 10080) * 60);
          return [owner.key, { ...value, finished, next_cursor: more || !old?.finished?.length ? value.next_cursor : old.next_cursor, owner }];
        } catch (e) {
          return [owner.key, { owner, active: [], finished: [], error: e.message, next_cursor: "" }];
        }
      }));
      if (!current()) return;
      for (const [key, value] of responses) if (value) this.results.set(key, value);
      this.render();
    } finally {
      this.pending.delete(requestKey);
      if (current()) this.more.disabled = false;
    }
  }
  render() {
    this.rows.replaceChildren();
    const values = [...this.results.values()], f = this.filters();
    const active = values.flatMap((v) => v.active.map((j) => ({ ...j, owner: v.owner })));
    const finished = values.flatMap((v) => v.finished.map((j) => ({ ...j, owner: v.owner }))).sort((a, b) => b.ended_at - a.ended_at);
    const failures = values.filter((v) => v.error);
    const hidden = values.reduce((n, v) => n + (v.requests_hidden || 0), 0);
    const totalFinished = values.reduce((n, v) => n + (v.finished_count || 0), 0);
    this.status.textContent = `${active.length} active · ${totalFinished} finished (${finished.length} loaded)` + (hidden ? ` · ${hidden} active HTTP requests hidden` : "");
    for (const v of values) {
      if (v.error) this.rows.append(el("p", v.owner.name + ": " + v.error, "stale-note"));
      else if (v.telemetry_stale) this.rows.append(el("p", v.owner.name + ": active work may be stale.", "stale-note"));
    }
    for (const [name, rows, cls] of [["Active jobs", active, "active-job"], ["Finished jobs", finished, "finished-job"]]) {
      if (f.state === "finished" && cls === "active-job" || f.state === "active" && cls === "finished-job") continue;
      const heading = el("h3", (f.activity === "requests" ? name.replace("jobs", "HTTP requests") : name) + " (" + rows.length + ")");
      this.rows.append(heading);
      if (!rows.length && !failures.length) this.rows.append(el("div", "No matching " + name.toLowerCase() + ".", "empty"));
      for (const job of rows) {
        const block = el("div", null, "job " + cls);
        block.dataset.jobId = job.id;
        block.dataset.ownerKey = job.owner.key;
        block.append(el("div", job.label || job.id, "job-label"),
          el("div", [job.owner.name + " / " + job.owner.plaza, job.status, job.kind].filter(Boolean).join(" · "), "job-meta"),
          el("div", job.id, "job-meta mono"));
        if (job.detail) block.append(el("div", job.detail, "job-detail"));
        if (job.error) block.append(el("div", job.error, "degraded job-detail"));
        block.append(el("div", (job.state === "finished" ? "Finished " + date(job.ended_at) : "Started " + date(job.started_at)) +
          (job.duration_ms == null ? "" : " · " + Number(job.duration_ms).toLocaleString(undefined, { maximumFractionDigits: 2 }) + " ms"), "job-meta"));
        const percent = typeof job.progress === "number" ? job.progress : job.progress?.percent;
        if (typeof percent === "number") { const bar = el("progress"); bar.max = 100; bar.value = percent; bar.setAttribute("aria-label", "Job progress"); block.append(bar); }
        block.append(this.actions(job.owner, job));
        this.rows.append(block);
      }
    }
    this.more.hidden = !values.some((v) => v.next_cursor);
  }
}
