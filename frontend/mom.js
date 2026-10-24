// Parent check-in screen: shows the newest unanswered check-in and records the answer.
const PERSON = "mom";
const $ = (id) => document.getElementById(id);

const THANKS = {
  was_me: ["Thank you", "That's all we needed. Nothing else to do."],
  not_me: ["Thank you for telling us",
    "Your family can see your answer and will reach out to you. Please don't reply to any calls, " +
    "texts or emails about these payments."],
  someone_asked: ["Thank you. Please stop for now.",
    "Don't send any more money or gift cards, even if they say it's urgent or a secret. " +
    "A real bank, the police or your grandchild will never be upset that you checked first. " +
    "Please call your family now."],
};

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

let current = null;   // decision id on screen
let answered = null;  // decision id just answered, so polling doesn't flip back

function show(id) {
  for (const s of ["allWell", "ask", "thanks"]) $(s).hidden = s !== id;
}

async function refresh() {
  const res = await fetch(`/people/${PERSON}/check-in`);
  const checkIn = res.ok ? await res.json() : null;
  if (!checkIn) {
    if (!answered) show("allWell");
    current = null;
    return;
  }
  if (checkIn.decision_id === current) return;  // don't re-render while she's reading
  answered = null;
  current = checkIn.decision_id;
  $("message").textContent = checkIn.message ||
    "We noticed some activity on your account that looks unusual. Was this you?";
  const list = $("events");
  list.replaceChildren(...checkIn.events.slice(0, 5).map((e) => {
    const li = document.createElement("li");
    const what = document.createElement("div");
    what.className = "what";
    what.textContent = describe(e);
    const at = document.createElement("div");
    at.className = "when";
    at.textContent = [e.description, when(e.when)].filter(Boolean).join(" · ");
    li.append(what, at);
    return li;
  }));
  $("error").hidden = true;
  show("ask");
}

for (const btn of document.querySelectorAll(".choices button")) {
  btn.onclick = async () => {
    if (!current) return;
    const answer = btn.dataset.answer;
    document.querySelectorAll(".choices button").forEach((b) => (b.disabled = true));
    try {
      const res = await fetch(`/decisions/${current}/parent-response`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answer }),
      });
      if (!res.ok) throw new Error();
      answered = current;
      const [title, body] = THANKS[answer];
      $("thanksTitle").textContent = title;
      $("thanksBody").textContent = body;
      show("thanks");
    } catch {
      $("error").textContent = "Something went wrong. Please try again, or call your family.";
      $("error").hidden = false;
    } finally {
      document.querySelectorAll(".choices button").forEach((b) => (b.disabled = false));
    }
  };
}

refresh().catch(() => show("allWell"));
setInterval(() => refresh().catch(() => {}), 3000);
