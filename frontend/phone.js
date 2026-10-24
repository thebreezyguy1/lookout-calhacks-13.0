// Demo stage: a scammer console, Mom's simulated phone, and what Lookout sees.
// Everything sent from here is labeled source=injector by the backend.
const PERSON = "mom";
const $ = (id) => document.getElementById(id);

const SCRIPTS = [
  {
    id: "grandparent", label: "Grandparent in jail (phone call)", channel: "call",
    sender: "Unknown caller · (213) 555-0186",
    lines: [
      "Grandma? It's me... I'm in trouble. I was in a car accident and the police arrested me.",
      "Please don't tell Mom and Dad, I'm so embarrassed.",
      "My lawyer says I need $2,000 for bail today or I stay in jail over the weekend.",
      "He says the court only takes Google Play gift cards. Can you go to CVS right now?",
      "When you have them, read me the numbers on the back. Please hurry, Grandma.",
    ],
    actions: [{ label: "Mom buys $500 in Google Play cards at CVS",
                events: [{ kind: "purchase", amount: 500, merchant: "CVS Pharmacy", description: "Google Play gift cards" }] }],
  },
  {
    id: "irs", label: "IRS arrest warrant (texts)", channel: "sms", sender: "+1 (202) 555-0147",
    lines: [
      "IRS NOTICE: You owe $4,380 in back taxes. A warrant for your arrest will be issued today.",
      "To avoid arrest, pay now through a Bitcoin ATM. Do not discuss this case with anyone.",
      "Reply YES for the wallet QR code. You have 2 hours.",
    ],
    actions: [
      { label: "Mom withdraws $3,000 in cash", events: [{ kind: "withdrawal", amount: 3000, description: "Cash withdrawal" }] },
      { label: "Mom puts $3,000 into a Bitcoin ATM",
        events: [{ kind: "purchase", amount: 3000, merchant: "Bitcoin Depot ATM", description: "Bitcoin purchase" }] },
    ],
  },
  {
    id: "tech", label: "Microsoft tech support (phone call)", channel: "call",
    sender: "Microsoft Support · (888) 555-0199",
    lines: [
      "Hello, this is Microsoft Support. Your computer has been hacked and your bank account is at risk.",
      "Please install AnyDesk so I can secure your computer right now.",
      "To protect your money, buy $500 in Target gift cards and give me the codes. Don't call your bank, they may be involved.",
    ],
    actions: [{ label: "Mom buys $500 in Target gift cards",
                events: [{ kind: "purchase", amount: 500, merchant: "Target", description: "Target gift cards" }] }],
  },
  {
    id: "romance", label: "Online sweetheart overseas (texts)", channel: "sms", sender: "Daniel",
    lines: [
      "Good morning my love. I couldn't sleep, I kept thinking about you.",
      "I'm stuck at the port in Lagos. Customs is holding my shipment and I need $1,500 to release it.",
      "Please send it by Zelle today. And please keep this between us, your kids wouldn't understand.",
    ],
    actions: [{ label: "Mom adds Daniel and sends $1,500 by Zelle",
                events: [{ kind: "new_payee", counterparty: "Daniel M.", description: "New Zelle payee" },
                         { kind: "transfer", amount: 1500, counterparty: "Daniel M.", description: "Zelle" }] }],
  },
  {
    id: "daughter", label: "Normal: daughter asks for a gift card (texts)", channel: "sms",
    sender: "Your daughter", saved: true,
    lines: ["Hi Mom! Could you grab a $50 gift card for Emma's birthday party on Saturday? Love you!"],
    actions: [{ label: "Mom buys a $50 Target gift card",
                events: [{ kind: "purchase", amount: 50, merchant: "Target", description: "Gift card - Emma's birthday" }] }],
  },
];

const TIER_LABEL = { log: "All clear", check_in: "Checking in with Mom", alert: "Family alerted", recommend_hold: "Hold recommended" };
const COMMS = new Set(["text_message", "call_transcript"]);
const progress = Object.fromEntries(SCRIPTS.map((s) => [s.id, 0]));
let tab = "messages";
let endedCalls = new Set();
let dismissed = null;       // check-in decision id the sheet was opened for
let sheetOpen = false;

const pct = (x) => `${Math.round((x ?? 0) * 100)}%`;
const clock = (iso) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

function el(tag, className, text) {
  const n = document.createElement(tag);
  if (className) n.className = className;
  if (text != null) n.textContent = text;
  return n;
}

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* not JSON */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json();
}

function showError(err) {
  $("error").textContent = err ? String(err.message || err) : "";
  $("error").hidden = !err;
}

// ---------- scammer console ----------

const current = () => SCRIPTS.find((s) => s.id === $("script").value);

function renderConsole() {
  const s = current();
  const i = progress[s.id];
  $("next").innerHTML = "";
  const b = el("b", null, i < s.lines.length ? `Next line ${i + 1} of ${s.lines.length} · ${s.channel === "call" ? "spoken on the call" : "text"}` : "Script finished");
  $("next").append(b, el("span", null, i < s.lines.length ? s.lines[i] : "Use Mom's bank activity below, or pick another script."));
  $("sendNext").disabled = i >= s.lines.length;
  $("actions").replaceChildren(...s.actions.map((a) => {
    const btn = el("button", "btn", a.label);
    btn.type = "button";
    btn.onclick = () => busy(btn, () => api(`/people/${PERSON}/events?auto_assess=true`,
      { method: "POST", body: JSON.stringify(a.events) }).then(refresh));
    return btn;
  }));
}

async function busy(btn, fn) {
  btn.disabled = true;
  showError(null);
  try { await fn(); } catch (e) { showError(e); } finally { btn.disabled = false; renderConsole(); }
}

function showHeard(result) {
  const box = $("heard");
  const c = result.classifier;
  box.hidden = false;
  box.replaceChildren(
    el("div", "muted", "What Lookout heard in that line"),
    (() => { const d = el("div", "chips"); (result.tactics.length ? result.tactics : ["nothing unusual"]).forEach((t) => d.append(el("span", "chip", t.replaceAll("_", " ")))); return d; })(),
    el("div", null, c ? `Claude: ${pct(c.likelihood)} likely a scam. ${c.reason}` : "Keyword rules only."),
  );
}

async function send(channel, sender, text, saved) {
  const result = await api(`/people/${PERSON}/comms`, {
    method: "POST",
    body: JSON.stringify({ channel, sender, text, saved_contact: !!saved, use_llm: $("useLlm").checked }),
  });
  if (channel === "call") { endedCalls.delete(sender); setTab("call"); } else { setTab("messages"); }
  showHeard(result);
  await refresh();
}

$("sendNext").onclick = (ev) => busy(ev.currentTarget, async () => {
  const s = current();
  const line = s.lines[progress[s.id]];
  if (!line) return;
  await send(s.channel, s.sender, line, s.saved);
  progress[s.id] += 1;
});

$("sendOwn").onclick = (ev) => busy(ev.currentTarget, async () => {
  const text = $("text").value.trim();
  if (!text) throw new Error("Type a message first.");
  await send(document.querySelector('input[name="ch"]:checked').value, $("sender").value.trim() || "Unknown", text, false);
  $("text").value = "";
});

$("resetBtn").onclick = (ev) => busy(ev.currentTarget, async () => {
  await api(`/people/${PERSON}/reset`, { method: "POST" });
  Object.keys(progress).forEach((k) => (progress[k] = 0));
  endedCalls = new Set();
  dismissed = null;
  closeSheet();
  $("heard").hidden = true;
  await refresh();
});

$("script").innerHTML = SCRIPTS.map((s) => `<option value="${s.id}">${s.label}</option>`).join("");
$("script").onchange = renderConsole;

// ---------- Mom's phone ----------

function setTab(t) {
  tab = t;
  $("tabMsgs").classList.toggle("on", t === "messages");
  $("tabCall").classList.toggle("on", t === "call");
}
$("tabMsgs").onclick = () => { setTab("messages"); refresh(); };
$("tabCall").onclick = () => { setTab("call"); refresh(); };

function renderPhone(events) {
  const comms = events.filter((e) => COMMS.has(e.kind));
  const view = $("view");
  if (tab === "call") {
    const calls = comms.filter((e) => e.kind === "call_transcript");
    const last = calls[calls.length - 1];
    if (!last || endedCalls.has(last.counterparty)) {
      view.replaceChildren(el("div", "empty-phone", last ? "Call ended." : "No active call."));
      return;
    }
    const lines = calls.filter((e) => e.counterparty === last.counterparty);
    const secs = Math.max(0, Math.round((Date.now() - new Date(lines[0].occurred_at).getTime()) / 1000));
    const cv = el("div", "callview");
    const top = el("div");
    top.append(el("div", "who", last.counterparty), el("div", "timer", `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`));
    const caps = el("div", "captions");
    caps.append(el("div", "label", "Live captions"), ...lines.map((e) => el("div", "cap", e.description)));
    const end = el("button", "endcall", "End");
    end.type = "button";
    end.onclick = () => { endedCalls.add(last.counterparty); renderPhone(events); };
    cv.append(top, caps, end);
    view.replaceChildren(cv);
    caps.scrollTop = caps.scrollHeight;
    return;
  }
  const texts = comms.filter((e) => e.kind === "text_message");
  const last = texts[texts.length - 1];
  if (!last) { view.replaceChildren(el("div", "empty-phone", "No messages yet.")); return; }
  const thread = texts.filter((e) => e.counterparty === last.counterparty);
  const head = el("div", "thread-head");
  head.append(el("div", "who", last.counterparty), el("div", "sub", last.raw?.saved_contact ? "Contact" : "Not in your contacts"));
  const bubbles = el("div", "bubbles");
  bubbles.append(...thread.map((e) => { const b = el("div", "bubble", e.description); b.append(el("time", null, clock(e.occurred_at))); return b; }));
  view.replaceChildren(head, bubbles);
  view.scrollTop = view.scrollHeight;
}

function openSheet(id) {
  dismissed = id;
  sheetOpen = true;
  if ($("momFrame").getAttribute("src") === "about:blank") $("momFrame").src = "mom.html?compact";
  $("sheet").hidden = false;
  $("banner").hidden = true;
  $("pill").hidden = true;
}
function closeSheet() {
  sheetOpen = false;
  $("sheet").hidden = true;
}
$("banner").onclick = () => openSheet(window.__checkIn);
$("minimize").onclick = () => { closeSheet(); $("pill").hidden = false; };
$("pill").onclick = () => openSheet(dismissed);

function renderLookout(decisions, checkIn) {
  const d = decisions[0];
  window.__checkIn = checkIn?.decision_id ?? null;
  // A new check-in drops a banner onto the phone, unless the sheet is already open.
  if (checkIn && checkIn.decision_id !== dismissed && !sheetOpen) $("banner").hidden = false;
  if (!checkIn && !sheetOpen) $("banner").hidden = true;

  const tier = $("tier");
  if (!d) {
    tier.className = "tierbig none"; tier.textContent = "Watching";
    $("tierMeta").textContent = "No concerns yet.";
    $("sigs").replaceChildren();
    return;
  }
  tier.className = `tierbig ${d.tier}`;
  tier.textContent = TIER_LABEL[d.tier];
  $("tierMeta").textContent = `Risk ${pct(d.assessment.risk_score)} · ${(d.assessment.pattern || "").replaceAll("_", " ") || "no pattern"}`
    + (d.requires_approval ? " · waiting for family approval" : "");
  $("sigs").replaceChildren(...[...d.assessment.signals].sort((a, b) => b.weight - a.weight).slice(0, 6).map((s) => {
    const li = el("li", s.rule === "scam_contact_then_payment" ? "link" : null);
    li.append(el("span", null, s.detail), el("span", "w", s.weight.toFixed(2)));
    return li;
  }));
}

async function refresh() {
  const [events, decisions, checkIn] = await Promise.all([
    api(`/people/${PERSON}/events`), api(`/people/${PERSON}/decisions`), api(`/people/${PERSON}/check-in`),
  ]);
  renderPhone(events);
  renderLookout(decisions, checkIn);
}

setInterval(() => { $("clock").textContent = new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); }, 10000);
$("clock").textContent = new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
renderConsole();
refresh().catch(showError);
setInterval(() => refresh().catch(() => {}), 2500);
