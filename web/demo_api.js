// Stands in for the service in the static demo (scripts/build_demo.py puts it in place of
// the page's api() function). DEMO holds responses recorded from the real service; verdicts
// are kept in this page only and are gone on reload.
const DEMO = __DEMO_DATA__;
const demo = { labels: {}, status: {}, receipts: {}, payloads: {}, drafted: {}, audit: { sent_mail: [], alerts: [], leads: [] } };
const copy = (value) => JSON.parse(JSON.stringify(value));
// Slider outcomes per recorded decision, found again by the numbers the page sends back.
const demoRoutes = new Map(Object.values(DEMO.decisions).map((decision, index) =>
  [JSON.stringify([decision.probabilities, decision.injection_probability]), Object.values(DEMO.routes)[index]]));
const textKey = (email) => JSON.stringify([email.from || "", email.subject || "", email.body || ""].map((part) => part.trim()));

function demoView(id) {
  const label = demo.labels[id];
  const view = copy(label ? DEMO.labelled[id][label] : DEMO.views[id]);
  view.actions = view.actions.map((action) => {
    if (!label && demo.drafted[action.key]) action = copy(DEMO.drafts[action.key]);
    if (demo.payloads[action.key]) action.payload = demo.payloads[action.key];
    action.status = demo.status[action.key] || "pending";
    action.receipt = demo.receipts[action.key] || null;
    return action;
  });
  return view;
}

function demoAction(id, kind) {
  const action = demoView(id).actions.find((a) => a.kind === kind);
  if (!action || action.status !== "pending") throw new Error(`'${kind}' is not waiting for a verdict on ${id}`);
  return action;
}

function demoApprove(id, kind, text) {
  const action = demoAction(id, kind);
  if (typeof text === "string") action.payload[action.text_field] = text;
  // What the mock API does with a write: number it, keep it, return it.
  const [list, prefix, status] = "to" in action.payload ? ["sent_mail", "mail", "sent"]
    : "channel" in action.payload ? ["alerts", "alert", "posted"] : ["leads", "lead", "created"];
  const record = { id: `${prefix}-${demo.audit[list].length + 1}`, ...action.payload };
  demo.audit[list].push(record);
  demo.payloads[action.key] = action.payload;
  demo.status[action.key] = "executed";
  demo.receipts[action.key] = { status, ...record };
  return demoView(id).actions.find((a) => a.key === action.key);
}

async function api(path, body) {
  await new Promise((done) => setTimeout(done, path.endsWith("/draft") ? 900 : 60));
  if (path === "/api/config") return DEMO.config;
  if (path === "/api/inbox") return DEMO.inbox;
  if (path === "/api/probes") return DEMO.probes;
  if (path === "/api/audit") return copy(demo.audit);
  if (path === "/api/decide") {
    const decision = DEMO.decisions[textKey(body)];
    if (!decision) throw new Error("This static demo replays answers recorded from the model for the inbox and the probe emails; a new or edited text needs the model running (see the repository).");
    return decision;
  }
  if (path === "/api/route") {
    const outcomes = demoRoutes.get(JSON.stringify([body.probabilities, body.injection_probability]));
    return DEMO.outcomes[outcomes[Math.round(body.threshold * 100) - 50]];
  }
  const [, id, rest] = path.match(/^\/api\/emails\/([^/]+)\/?(.*)$/) || [];
  if (!id || !DEMO.views[id]) throw new Error("not found: " + path);
  if (!rest) return demoView(id);
  if (rest === "label") {
    if (!DEMO.labelled[id]) throw new Error("the model labelled this email: reject its actions instead");
    if (body.label) demo.labels[id] = body.label; else delete demo.labels[id];
    return demoView(id);
  }
  const [, kind, verb] = rest.match(/^actions\/([^/]+)\/(approve|reject|draft)$/) || [];
  if (verb === "approve") return demoApprove(id, kind, body && body.text);
  if (verb === "reject") {
    const action = demoAction(id, kind);
    demo.status[action.key] = "rejected";
    return demoView(id).actions.find((a) => a.key === action.key);
  }
  if (verb === "draft") {
    const action = demoAction(id, kind);
    if (demo.labels[id] || !DEMO.drafts[action.key]) throw new Error("no draft was recorded for this case in the static demo");
    demo.drafted[action.key] = true;
    return demoView(id).actions.find((a) => a.key === action.key);
  }
  throw new Error("not found: " + path);
}
