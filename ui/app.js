"use strict";
const $ = (id) => document.getElementById(id);
const node = (tag, text, cls) => {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
};
let tasks = [],
  selected = Number(location.hash.slice(1)) || null,
  openedRun = null,
  logKind = "stdout",
  busy = false;
const time = (value) => (value ? new Date(value).toLocaleString() : "—");
async function api(path) {
  const r = await fetch(path, { cache: "no-store" });
  if (!r.ok) throw Error(`Request failed (${r.status})`);
  return r.json();
}
function renderList() {
  const q = $("filter").value.toLowerCase();
  $("tasks").replaceChildren();
  for (const t of tasks.filter((t) =>
    `${t.id} ${t.title} ${t.status}`.toLowerCase().includes(q),
  )) {
    const b = node(
      "button",
      undefined,
      "task" + (t.id === selected ? " selected" : ""),
    );
    b.append(
      node("small", `#${String(t.id).padStart(5, "0")} · ${t.status}`),
      node("span", t.title),
    );
    b.onclick = () => {
      selected = t.id;
      location.hash = t.id;
      refresh();
    };
    $("tasks").append(b);
  }
  if (!$("tasks").children.length)
    $("tasks").append(node("p", "No matching tasks.", "empty"));
}
function detailBlock(title, text) {
  const d = node("details");
  d.append(
    node("summary", title),
    node("pre", text || "No evidence recorded yet."),
  );
  return d;
}
function renderDetail(t) {
  const a = $("detail");
  const sameTask = a.dataset.taskId === String(t.id);
  const openSections = new Set(
    sameTask
      ? [...a.querySelectorAll("details[open] > summary")].map(
          (e) => e.textContent,
        )
      : [],
  );
  a.dataset.taskId = t.id;
  a.replaceChildren();
  a.append(
    node("span", t.status, `badge ${t.status.toLowerCase()}`),
    node("h2", `#${t.id} ${t.title}`),
  );
  const meta = node("div", undefined, "meta");
  meta.append(
    node("span", `Attempt ${t.attempt}/${t.max_attempts}`),
    node("span", `Replans ${t.replans}`),
    node("span", `Plan r${t.plan_revision || 0} · Feedback r${t.feedback_revision || 0}`),
    node("span", `Received ${time(t.created_at)}`),
  );
  if (t.parent_task_id)
    meta.append(node("span", `Recovery of #${t.parent_task_id}`));
  a.append(meta);
  if ((t.agent_diagnoses || []).length) a.append(detailBlock('Agent health consultations', JSON.stringify(t.agent_diagnoses, null, 2)));
  for (const job of t.experiment_jobs || []) {
    const box = node('section');
    box.append(node('h3', `Experiment ${job.id.slice(0, 8)} · ${job.state}`));
    box.append(node('p', `${job.stage}: ${job.completed}/${job.total || '?'} · last useful progress ${Math.round(job.progress_age_seconds)}s ago · telemetry ${job.telemetry_status} · W&B ${job.wandb_status || 'unconfigured'}`));
    box.append(node('p', `Heartbeat age: ${job.heartbeat_age_seconds == null ? 'unknown' : Math.round(job.heartbeat_age_seconds) + 's'} · last observation: ${job.telemetry_age_seconds == null ? 'unknown' : Math.round(job.telemetry_age_seconds) + 's ago'}`));
    if (job.total) {
      const progress = node('progress'); progress.max = job.total; progress.value = job.completed; box.append(progress);
    }
    if (job.wandb_url && /^https:\/\/(wandb\.ai|(?:wandb\.)?forge\.coreweave\.com)\//.test(job.wandb_url)) {
      const link = node('a', 'Open W&B run'); link.href = job.wandb_url; link.target = '_blank'; link.rel = 'noopener noreferrer'; box.append(link);
    }
    if (job.diagnosis && job.diagnosis.assessment) box.append(detailBlock('Read-only stall diagnosis', JSON.stringify(job.diagnosis, null, 2)));
    if (job.metrics && Object.keys(job.metrics).length) box.append(detailBlock('Latest experiment metrics', JSON.stringify(job.metrics, null, 2)));
    a.append(box);
  }
  const stages = node("div", undefined, "stages");
  const order = [
    "PENDING",
    "RESEARCHING",
    "OPERATING",
    "VALIDATING",
    "COMMUNICATING",
    "COMPLETED",
  ];
  const labels = [
    "Queue",
    "Research",
    "Execute",
    "Check + validate",
    "Report",
    "Complete",
  ];
  const index = order.indexOf(t.status);
  for (let i = 0; i < order.length; i++)
    stages.append(
      node(
        "span",
        labels[i],
        "stage" + (i === index ? " current" : i < index ? " done" : ""),
      ),
    );
  a.append(stages);
  if (index < 0)
    a.append(
      node(
        "p",
        t.status.startsWith("WAITING_")
          ? `Waiting: ${t.wait?.reason || t.status}. Worker slot released; no model is called while waiting.`
          : "Execution stopped. Inspect validation feedback and run evidence.",
      ),
    );
  if (t.wait) a.append(detailBlock("Wait dependency and deadline", JSON.stringify(t.wait, null, 2)));
  if (t.approvals?.length) a.append(detailBlock("Exact action approvals (read only)", JSON.stringify(t.approvals, null, 2)));
  if (t.user_feedback?.length) a.append(detailBlock("User feedback revisions", JSON.stringify(t.user_feedback, null, 2)));
  const progress = node("progress");
  progress.max = t.total_steps;
  progress.value = t.verified_steps;
  a.append(
    node(
      "p",
      `${t.verified_steps}/${t.total_steps} milestones verified · ${t.step_title}`,
    ),
    progress,
  );
  if (t.worker_owned)
    a.append(
      node(
        "p",
        `Worker heartbeat: ${t.heartbeat_age_seconds ?? "unknown"} seconds ago`,
      ),
    );
  const list = node("ol", undefined, "milestones");
  for (let i = 0; i < (t.plan.steps || []).length; i++)
    list.append(
      node(
        "li",
        `${i < t.verified_steps ? "✓ " : i === t.step_index ? "→ " : ""}${t.plan.steps[i].title || t.plan.steps[i].instructions}`,
      ),
    );
  a.append(list);
  a.append(
    detailBlock(
      "Request and workspace",
      `${t.request}\n\nWorkspace: ${t.workspace}`,
    ),
    detailBlock("Result", t.result),
    detailBlock("Validation", t.validation),
  );
  a.append(
    node("h3", "Agent runs"),
    node(
      "p",
      "Process completion records execution. The validator and executable checks determine task success.",
    ),
  );
  for (const r of t.runs) {
    const row = node("div", undefined, "run"),
      head = node("div", undefined, "run-head");
    head.append(
      node("code", r.run_label),
      node("span", r.status, `badge ${r.status.toLowerCase()}`),
    );
    row.append(
      head,
      node(
        "p",
        `${r.provider_name} · ${time(r.spawned_at)} → ${time(r.shutdown_at)} · ${r.duration_seconds.toFixed(1)}s · exit ${r.exit_code}`,
      ),
    );
    if (r.session_id)
      row.append(node("p", `Provider session: ${r.session_id}`));
    if (r.error_text) row.append(node("p", r.error_text));
    const b = node("button", "Inspect prompt / transcript / result");
    b.onclick = () => openLog(r);
    row.append(b);
    a.append(row);
  }
  a.append(
    detailBlock(
      "Executable check history",
      t.checks.length
        ? JSON.stringify(t.checks, null, 2)
        : "No executable checks recorded. Inspect the validator evidence.",
    ),
  );
  const events = node("details");
  events.append(node("summary", "Controller events"));
  for (const e of t.events) {
    const row = node("div", undefined, "event");
    row.append(
      node("time", time(e.timestamp)),
      node("strong", e.event_type),
      node("div", e.summary),
    );
    events.append(row);
  }
  a.append(events);
  for (const d of a.querySelectorAll("details"))
    d.open = openSections.has(d.querySelector("summary").textContent);
  if (openedRun)
    openedRun = t.runs.find((r) => r.id === openedRun.id) || openedRun;
}
async function loadLog() {
  if (!openedRun) return;
  try {
    const r = await api(`/api/runs/${openedRun.id}/log?kind=${logKind}`);
    $("log-text").textContent = r.text;
    $("log-note").textContent = r.truncated
      ? "Showing the final 64 KB; the full transcript remains on disk."
      : `${r.bytes} bytes · ${openedRun.status === "RUNNING" ? "live output" : "saved output"}`;
  } catch (e) {
    $("log-text").textContent =
      "This transcript is not available yet. Try another tab.";
    $("log-note").textContent = "";
  }
}
function openLog(r) {
  openedRun = r;
  logKind = "stdout";
  $("log-title").textContent = r.run_label;
  $("log-tabs").replaceChildren();
  for (const k of ["stdout", "stderr", "prompt", "result", "final", "native"]) {
    const b = node("button", k);
    b.onclick = () => {
      logKind = k;
      loadLog();
    };
    $("log-tabs").append(b);
  }
  $("transcript").showModal();
  loadLog();
}
async function refresh() {
  if (busy) return;
  busy = true;
  try {
    const data = await api("/api/tasks");
    tasks = data.tasks;
    $("counts").replaceChildren();
    for (const [label, value] of [
      ["Queued", data.counts.PENDING || 0],
      [
        "Working",
        Object.entries(data.counts)
          .filter(
            ([k]) =>
              !["PENDING", "COMPLETED", "ESCALATED", "FAILED"].includes(k),
          )
          .reduce((s, [, v]) => s + v, 0),
      ],
      ["Completed", data.counts.COMPLETED || 0],
      [
        "Needs attention",
        (data.counts.ESCALATED || 0) + (data.counts.FAILED || 0),
      ],
    ]) {
      const box = node("div", undefined, "count");
      box.append(node("strong", value), node("span", label));
      $("counts").append(box);
    }
    renderList();
    if (selected) renderDetail(await api(`/api/tasks/${selected}`));
    $("connection").textContent =
      `Connected · ${new Date().toLocaleTimeString()}`;
    if ($("transcript").open) await loadLog();
  } catch (e) {
    $("connection").textContent = "Disconnected · retrying";
  } finally {
    busy = false;
  }
}
$("filter").oninput = renderList;
$("close-log").onclick = () => {
  $("transcript").close();
  openedRun = null;
};
window.addEventListener("hashchange", () => {
  selected = Number(location.hash.slice(1)) || null;
  refresh();
});
refresh();
setInterval(refresh, 3000);
