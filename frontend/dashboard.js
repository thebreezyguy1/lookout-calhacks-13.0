// Family dashboard. Reads everything from the Lookout API on the same origin.
const PERSON = "mom";
const $ = (id) => document.getElementById(id);

const TIER_LABEL = {
  log: "All clear",
  check_in: "Checking in with Mom",
  alert: "Family alerted",
  recommend_hold: "Hold recommended",
};
const ANSWER_LABEL = {
  was_me: ["Mom says: \"Yes, that was me.\"", "ok"],
  not_me: ["Mom says: \"No, that wasn't me.\" Possible identity theft or account takeover.", "bad"],
  someone_asked: ["Mom says: \"Someone asked me to do this.\" This matches a scam in progress. Call her now.", "bad"],
};

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const pct = (x) => `${Math.round((x ?? 0) * 100)}%`;
const money = (x) => (x == null ? "" : x.toLocaleString("en-US", { style: "currency", currency: "USD" }));
const time = (iso) => new Date(iso).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" });
const clock = (iso) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const nice = (s) => (s ? s.replaceAll("_", " ") : "-");

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* not JSON */ }
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    err.status = res.status;
    throw err;
  }
  return res.json();
}

function showError(id, err) {
  const el = $(id);
  el.textContent = err ? String(err.message || err) : "";
  el.hidden = !err;
}

async function busy(btn, fn) {
  btn.disabled = true;
  try { await fn(); } finally { btn.disabled = false; }
}

// ---------- render ----------

function renderStatus(d) {
  const el = $("status");
  if (!d) {
    el.innerHTML = `<h2>Right now</h2><div class="empty">No assessment yet. Use the simulation panel to inject a scenario, then assess.</div>`;
    return;
  }
  const a = d.assessment;
  const color = `var(--${{ log: "log", check_in: "check", alert: "alert", recommend_hold: "hold" }[d.tier]})`;
  el.innerHTML = `
    <h2>Right now</h2>
    <div class="tier ${d.tier}">${TIER_LABEL[d.tier]}</div>
    <div class="metrics">
      <div class="metric" style="color:${color}"><div class="k">Risk</div><div class="v" style="color:var(--text)">${pct(a.risk_score)}</div><div class="meter"><i style="width:${pct(a.risk_score)}"></i></div></div>
      <div class="metric"><div class="k">Confidence</div><div class="v">${pct(a.confidence)}</div></div>
      <div class="metric"><div class="k">Pattern</div><div class="v">${esc(nice(a.pattern))}</div></div>
      <div class="metric"><div class="k">Engine</div><div class="v">${a.engine === "rules+llm" ? "Rules + Claude" : "Rules only"}</div></div>
    </div>
    ${a.family_summary ? `<div class="summary">${esc(a.family_summary)}</div>` : ""}
    ${a.reasoning && a.reasoning !== a.family_summary ? `<div class="reasoning">${esc(a.reasoning)}</div>` : ""}`;
}

function renderApproval(d) {
  const box = $("approval");
  if (!d || !d.requires_approval) { box.hidden = true; return; }
  box.hidden = false;
  const done = d.approved !== null;
  $("approveBtn").hidden = $("rejectBtn").hidden = $("reviewer").hidden = done;
  let note = box.querySelector(".note");
  if (done) {
    if (!note) { note = document.createElement("div"); box.appendChild(note); }
    note.className = `note ${d.approved ? "bad" : "ok"}`;
    note.style.marginTop = "12px";
    note.textContent = d.approved
      ? `Approved by ${d.reviewed_by}. Next step: call Mom's bank fraud line and ask them to pause outgoing payments.`
      : `Rejected by ${d.reviewed_by}. No hold recommended.`;
  } else if (note) {
    note.remove();
  }
}

function renderMomAnswer(d) {
  const box = $("momAnswer");
  if (!d || d.tier === "log") { box.hidden = true; return; }
  box.hidden = false;
  if (!d.parent_response) {
    $("momAnswerBody").innerHTML = `<div class="note">Mom has been asked on her screen and hasn't answered yet.${d.assessment.parent_message ? `<br><span class="muted">"${esc(d.assessment.parent_message)}"</span>` : ""}</div>`;
    return;
  }
  const [text, kind] = ANSWER_LABEL[d.parent_response];
  $("momAnswerBody").innerHTML = `<div class="note ${kind}">${esc(text)} <span class="muted small">(${clock(d.parent_responded_at)})</span></div>`;
}

function renderSignals(d) {
  const list = $("signals");
  const signals = d?.assessment.signals || [];
  if (!signals.length) { list.innerHTML = `<li class="empty">No signals.</li>`; return; }
  list.innerHTML = [...signals].sort((x, y) => y.weight - x.weight).map((s) => `
    <li class="signal">
      <div>${esc(s.detail)}</div><div class="w">${esc(s.rule)} · ${s.weight.toFixed(2)}</div>
      <div class="bar"><i style="width:${s.weight * 100}%"></i></div>
    </li>`).join("");
}

function renderEvents(events, d) {
  const body = $("events").querySelector("tbody");
  const cutoff = Date.now() - 72 * 3600 * 1000;
  const recent = events.filter((e) => new Date(e.occurred_at).getTime() >= cutoff).reverse();
  if (!recent.length) { body.innerHTML = `<tr><td class="empty">No events in the last 72 hours.</td></tr>`; return; }
  const flagged = new Set((d?.assessment.signals || []).flatMap((s) => s.event_ids));
  body.innerHTML = recent.map((e) => `
    <tr class="${flagged.has(e.id) ? "flag" : ""}">
      <td class="muted" style="white-space:nowrap">${time(e.occurred_at)}</td>
      <td>${esc(nice(e.kind))}</td>
      <td>${esc(e.merchant || e.counterparty || "")}<div class="muted small">${esc(e.description || "")}</div></td>
      <td class="amt">${money(e.amount)}</td>
      <td><span class="tag">${esc(e.source)}</span></td>
    </tr>`).join("");
}

function renderBaseline(b) {
  const el = $("baseline");
  if (!b) { el.innerHTML = `<div class="empty" style="margin-bottom:12px">No baseline yet. Pull Mom's real credit file to set what "normal" looks like.</div>`; return; }
  $("who").textContent = `Watching over ${b.name ? b.name.toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase()) : "Mom"}${b.persona_id ? ` (CRS ${b.persona_id})` : ""}`;
  el.innerHTML = `
    <dl class="kv">
      <dt>File</dt><dd>${b.dormant_file ? `<span class="pill on">Dormant</span>` : `<span class="pill off">Active</span>`}</dd>
      <dt>Accounts</dt><dd>${b.total_accounts} total, ${b.open_accounts} open</dd>
      <dt>Last new account</dt><dd>${b.months_since_last_new_account == null ? "-" : `${b.months_since_last_new_account} months ago`}</dd>
      <dt>Inquiries (24 mo)</dt><dd>${b.inquiries_last_24m}</dd>
      <dt>Score</dt><dd>${b.score ?? "None (stale file)"}</dd>
      <dt>Fraud Finder</dt><dd>${b.fraud_score == null ? "-" : `${b.fraud_score} / 100`}</dd>
    </dl>
    ${b.dormant_file ? `<p class="small muted" style="margin-top:0">Any new credit on a dormant file is a strong identity-theft signal.</p>` : ""}
    ${b.bureau_errors.length ? `<div class="error">${b.bureau_errors.map(esc).join("<br>")}</div>` : ""}`;
}

function renderHistory(decisions, selectedId) {
  const list = $("history");
  if (!decisions.length) { list.innerHTML = `<li class="empty">None yet.</li>`; return; }
  list.innerHTML = decisions.slice(0, 8).map((d) => `
    <li><span class="tier ${d.tier}" style="font-size:12px;padding:2px 8px">${TIER_LABEL[d.tier]}</span>
        <span class="muted">${pct(d.assessment.risk_score)} · ${clock(d.created_at)}${d.id === selectedId ? " · current" : ""}</span></li>`).join("");
}

const AUDIT_LABEL = {
  baseline_built: (x) => `Baseline pulled from CRS (${x.persona}, ${x.bureau})${x.dormant ? ", dormant file" : ""}`,
  scenario_injected: (x) => `Simulated scenario: ${x.scenario} (${x.events} events)`,
  nessie_synced: (x) => `Nessie sync: ${x.new_events} new events`,
  assessed: (x) => `Assessed ${x.events} events → ${TIER_LABEL[x.tier]} (risk ${pct(x.risk)}, ${x.engine})`,
  approved: (x) => `Hold approved by ${x.reviewer}`,
  rejected: (x) => `Hold rejected by ${x.reviewer}`,
  parent_responded: (x) => `Mom answered: ${nice(x.answer)}`,
};

function renderAudit(records) {
  const list = $("audit");
  if (!records.length) { list.innerHTML = `<li class="empty">Empty.</li>`; return; }
  list.innerHTML = [...records].reverse().map((r) => `
    <li><time>${clock(r.at)}</time><span>${esc((AUDIT_LABEL[r.action] || ((x) => r.action))(r.detail))}</span></li>`).join("");
}

// ---------- data ----------

let latest = null;

async function refresh() {
  const [decisions, events, audit, baseline] = await Promise.all([
    api(`/people/${PERSON}/decisions`),
    api(`/people/${PERSON}/events`),
    api(`/people/${PERSON}/audit`),
    api(`/people/${PERSON}/baseline`).catch((e) => (e.status === 404 ? null : Promise.reject(e))),
  ]);
  latest = decisions[0] || null;
  renderStatus(latest);
  renderApproval(latest);
  renderMomAnswer(latest);
  renderSignals(latest);
  renderEvents(events, latest);
  renderBaseline(baseline);
  renderHistory(decisions, latest?.id);
  renderAudit(audit);
}

async function loadScenarios() {
  const scenarios = await api("/scenarios");
  const group = (label) => scenarios.filter((s) => s.label === label)
    .map((s) => `<option value="${esc(s.id)}" title="${esc(s.notes)}">${esc(s.id.replace(/^(scam|normal)-/, "").replaceAll("-", " "))}</option>`).join("");
  $("scenario").innerHTML = `<optgroup label="Scams">${group("scam")}</optgroup><optgroup label="Normal life">${group("normal")}</optgroup>`;
}

// ---------- actions ----------

$("baselineBtn").onclick = (ev) => busy(ev.currentTarget, async () => {
  showError("baselineError", null);
  try { await api(`/people/${PERSON}/baseline?persona=P20`, { method: "POST" }); await refresh(); }
  catch (e) { showError("baselineError", e); }
});

$("injectBtn").onclick = (ev) => busy(ev.currentTarget, async () => {
  showError("simError", null);
  try { await api(`/people/${PERSON}/inject/${encodeURIComponent($("scenario").value)}`, { method: "POST" }); await refresh(); }
  catch (e) { showError("simError", e); }
});

$("assessBtn").onclick = (ev) => busy(ev.currentTarget, async () => {
  showError("simError", null);
  try { await api(`/people/${PERSON}/assess?use_llm=${$("useLlm").checked}`, { method: "POST" }); await refresh(); }
  catch (e) { showError("simError", e); }
});

$("resetBtn").onclick = (ev) => busy(ev.currentTarget, async () => {
  showError("simError", null);
  try { await api(`/people/${PERSON}/reset`, { method: "POST" }); await refresh(); }
  catch (e) { showError("simError", e); }
});

async function review(approve, btn) {
  if (!latest) return;
  const reviewer = $("reviewer").value.trim();
  if (!reviewer) { showError("approvalError", "Enter your name so the audit log shows who decided."); return; }
  await busy(btn, async () => {
    showError("approvalError", null);
    try {
      await api(`/decisions/${latest.id}/review`, { method: "POST", body: JSON.stringify({ approve, reviewer }) });
      await refresh();
    } catch (e) { showError("approvalError", e); }
  });
}
$("approveBtn").onclick = (ev) => review(true, ev.currentTarget);
$("rejectBtn").onclick = (ev) => review(false, ev.currentTarget);

loadScenarios().catch((e) => showError("simError", e));
refresh().catch((e) => showError("simError", e));
// Poll so Mom's answer from her own screen shows up here live.
setInterval(() => refresh().catch(() => {}), 3000);
