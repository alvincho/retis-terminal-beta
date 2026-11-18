/** Job-scoped diagnostics in a dialog that preserves the graph inspector. */
function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
function timestamp(value) {
  if (!value) return null;
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(+date) ? null : +date / 1000;
}
export function jobLogQuery(job) {
  const query = { limit: 100 };
  const recovered = Object.hasOwn(job, "log_job_id");
  const id = recovered ? job.log_job_id : job.id;
  if (id) query.job_id = id;
  if (job.trace_id) query.trace_id = job.trace_id;
  if (job.span_id) query.span_id = job.span_id;
  if (recovered) {
    if (!job.trace_id || !job.span_id) throw Error("This older job has no execution-specific log context.");
    // Legacy jobs can share a caller's job ID and trace. Retrieve a narrow
    // time window and enforce the execution span again at the Plaza.
    const start = timestamp(job.started_at), end = timestamp(job.ended_at);
    if (start != null) query.since = Math.max(0, start - 1);
    if (end != null) query.until = end + 1;
  } else if (!id) throw Error("This job has no log context.");
  return query;
}
export function renderLogEntries(container, items) {
  container.replaceChildren();
  for (const item of [...items].sort((a, b) => b.cursor - a.cursor)) {
    const date = new Date(typeof item.timestamp === "number" ? item.timestamp * 1000 : item.timestamp);
    const time = Number.isNaN(+date) ? "—" : date.toLocaleTimeString(undefined, { hour12: false });
    const row = el("div", null, "log-line " + item.level);
    row.append(el("div", time + " " + item.level + (item.job_id ? " / " + item.job_id : "")),
      el("div", item.message || item.event || "", "log-message"));
    const fields = Object.fromEntries(Object.entries(item).filter(([k]) =>
      !["timestamp", "level", "message", "cursor", "captured_at", "agent_id", "instance_id"].includes(k)));
    if (Object.keys(fields).length) {
      const details = el("details");
      details.append(el("summary", "Details"), el("pre", JSON.stringify(fields, null, 2)));
      row.append(details);
    }
    container.append(row);
  }
}
export class JobLogsPopup {
  constructor({ api, token, allowed }) {
    Object.assign(this, { api, token, allowed });
    this.dialog = document.querySelector("#job-log-dialog");
    this.status = this.dialog.querySelector(".job-log-status");
    this.rows = this.dialog.querySelector(".job-popup-logs");
    this.level = this.dialog.querySelector("select");
    this.refreshButton = this.dialog.querySelector(".job-log-refresh");
    this.moreButton = this.dialog.querySelector(".job-log-more");
    this.level.onchange = () => this.retrieve();
    this.refreshButton.onclick = () => this.retrieve();
    this.moreButton.onclick = () => this.retrieve(true);
    this.dialog.addEventListener("close", () => {
      if (this.dialog.open) return;
      const row = [...document.querySelectorAll(".job")].find((node) =>
        node.dataset.ownerKey === this.owner?.key && node.dataset.jobId === this.job?.id);
      this.clear();
      row?.querySelector(".edge-actions button:last-child")?.focus({ preventScroll: true });
    });
    this.revision = 0;
  }
  clear() {
    this.revision++; clearTimeout(this.timer);
    this.request = null; this.owner = null; this.job = null; this.accountToken = null; this.items = []; this.nextCursor = null;
    this.rows.replaceChildren(); this.status.textContent = "";
    for (const field of this.dialog.querySelectorAll(".job-log-label, .job-log-owner, .job-log-id")) field.textContent = "";
    this.moreButton.hidden = true;
  }
  close() { if (this.dialog.open) this.dialog.close(); this.clear(); }
  revalidate() {
    if (this.dialog.open && (this.accountToken !== this.token() || !this.allowed(this.owner))) this.close();
  }
  open(owner, job) {
    this.close();
    this.owner = { ...owner }; this.job = { ...job }; this.accountToken = this.token();
    this.dialog.querySelector(".job-log-label").textContent = job.label || job.id;
    this.dialog.querySelector(".job-log-owner").textContent = owner.name + " / " + owner.plaza;
    this.dialog.querySelector(".job-log-id").textContent = job.id;
    this.level.value = "";
    this.dialog.showModal();
    this.retrieve();
  }
  current(revision) {
    this.revalidate();
    return this.dialog.open && this.revision === revision;
  }
  busy(value) {
    this.refreshButton.disabled = value; this.moreButton.disabled = value;
  }
  async retrieve(older = false) {
    const revision = ++this.revision;
    clearTimeout(this.timer); this.request = null;
    if (!this.current(revision)) return;
    if (!older) { this.items = []; this.rows.replaceChildren(); this.moreButton.hidden = true; }
    this.busy(true); this.status.textContent = "Retrieving this job’s logs…";
    try {
      const query = { ...jobLogQuery(this.job), level: this.level.value };
      if (older && this.nextCursor) query.before = this.nextCursor;
      const path = "/plazas/" + encodeURIComponent(this.owner.source) + "/agents/" + encodeURIComponent(this.owner.id) + "/logs";
      const value = await this.api(path, query);
      if (!this.current(revision)) return;
      this.request = { id: value.id, source: this.owner.source, started: Date.now(), older };
      this.status.textContent = "Waiting for agent heartbeat…";
      this.poll(revision);
    } catch (error) { this.fail(error, revision); }
  }
  fail(error, revision) {
    if (!this.current(revision)) return;
    this.request = null; this.busy(false);
    this.status.textContent = error.message;
    if (error.status === 401 || error.status === 403) { this.items = []; this.rows.replaceChildren(); this.moreButton.hidden = true; }
  }
  async poll(revision) {
    if (!this.current(revision) || !this.request) return;
    const request = this.request;
    try {
      const value = await this.api("/plazas/" + encodeURIComponent(request.source) + "/diagnostics/" + encodeURIComponent(request.id));
      if (!this.current(revision) || this.request !== request) return;
      if (value.status !== "ready") {
        if (Date.now() - request.started > 120000) throw Error("Log request expired. Refresh to retry.");
        this.timer = setTimeout(() => this.poll(revision), 2000);
        return;
      }
      const result = value.result;
      if (!Array.isArray(result?.items)) throw Error("Invalid log response.");
      this.items = [...new Map([...(request.older ? this.items : []), ...result.items].map((item) => [item.cursor, item])).values()].slice(-1000);
      this.nextCursor = result.next_cursor || Math.min(...result.items.map((item) => item.cursor));
      this.moreButton.hidden = !result.has_more && !(result.cached && result.items.length === 100);
      renderLogEntries(this.rows, this.items);
      if (!this.items.length) this.rows.append(el("div", this.moreButton.hidden ? "No retained logs match this job." : "No matching entries on this page. Load earlier to continue.", "empty"));
      this.status.textContent = this.items.length + " job log entries" + (result.cached ? " · Cached" : "");
      this.request = null; this.busy(false);
    } catch (error) { this.fail(error, revision); }
  }
}
