// Parent check-in: a calm conversation about the payments Lookout noticed.
const PERSON = "mom";
const $ = (id) => document.getElementById(id);

const money = (x) => (x == null ? "" : x.toLocaleString("en-US", { style: "currency", currency: "USD" }));
const when = (iso) => new Date(iso).toLocaleString([], { weekday: "long", hour: "numeric", minute: "2-digit" });

function describe(e) {
  const where = e.where ? ` at ${e.where}` : "";
  switch (e.kind) {
    case "purchase": return `${money(e.amount)}${where}`;
    case "transfer": return `${money(e.amount)} sent to ${e.where || "someone"}`;
    case "withdrawal": return `${money(e.amount)} taken out in cash`;
    case "new_payee": return `New person added to pay: ${e.where || ""}`;
    case "credit_inquiry": return `Someone applied for credit in your name${e.where ? ` with ${e.where}` : ""}`;
    case "new_credit_account": return `A new account was opened in your name${e.where ? ` with ${e.where}` : ""}`;
    default: return e.description || "Activity on your account";
  }
}

let current = null;   // check-in decision id on screen
let finished = false; // keep a finished conversation on screen until a new check-in arrives
let lastSpoken = "";

function show(id) {
  for (const s of ["allWell", "talk"]) $(s).hidden = s !== id;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function renderEvents(events) {
  $("events").replaceChildren(...events.slice(0, 4).map((e) => {
    const li = el("li");
    li.append(el("div", "what", describe(e)),
      el("div", "when", [e.description, when(e.when)].filter(Boolean).join(" · ")));
    return li;
  }));
}

function render(conv) {
  const lookoutMsgs = conv.messages.filter((m) => m.role === "lookout");
  const last = lookoutMsgs[lookoutMsgs.length - 1];
  $("chat").replaceChildren(...conv.messages.map((m) =>
    el("div", `msg ${m.role}${m === last ? " latest" : ""}`, m.text)));
  $("choices").replaceChildren(...conv.suggested_replies.map((r) => {
    const b = el("button", null, r);
    b.type = "button";
    b.onclick = () => send(r);
    return b;
  }));
  finished = conv.done;
  $("answer").hidden = conv.done;
  $("finished").hidden = !conv.done;
  lastSpoken = last ? last.text : "";
  (conv.done ? $("finished") : $("choices").querySelector("button") || $("ownText"))
    ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function busy(on) {
  document.querySelectorAll("#answer button, #answer input").forEach((n) => (n.disabled = on));
}

async function post(body) {
  const res = await fetch(`/decisions/${current}/conversation`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(res.statusText);
  return res.json();
}

async function send(text) {
  if (!current || !text.trim()) return;
  busy(true);
  $("error").hidden = true;
  try {
    render(await post({ message: text }));
    $("ownText").value = "";
  } catch {
    $("error").textContent = "That didn't go through. Please try again, or call your family.";
    $("error").hidden = false;
  } finally {
    busy(false);
  }
}

$("own").addEventListener("submit", (ev) => { ev.preventDefault(); send($("ownText").value); });

$("speak").onclick = () => {
  if (!("speechSynthesis" in window) || !lastSpoken) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(lastSpoken);
  u.rate = 0.9;
  speechSynthesis.speak(u);
};

async function refresh() {
  const res = await fetch(`/people/${PERSON}/check-in`);
  const checkIn = res.ok ? await res.json() : null;
  if (!checkIn) {
    if (!finished) { current = null; show("allWell"); }
    return;
  }
  if (checkIn.decision_id === current) return;   // same conversation, nothing to reload
  current = checkIn.decision_id;
  renderEvents(checkIn.events);
  show("talk");
  render(await post({}));                         // opens the conversation, or resumes it
}

refresh().catch(() => show("allWell"));
setInterval(() => refresh().catch(() => {}), 3000);
