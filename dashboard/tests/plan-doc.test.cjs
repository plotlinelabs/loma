const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

const context = vm.createContext({ exports: {} });
vm.runInContext(
  ts.transpileModule(fs.readFileSync(path.join(__dirname, "../src/lib/plan-doc.ts"), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText,
  context,
);
const { parsePlan, diffPlans } = context.exports;
const plain = (x) => JSON.parse(JSON.stringify(x));

const V1 = `# Slack alerts for failed syncs

**Summary:** Failed syncs are silent. We'll alert the owning team in Slack.
**Size:** M, about 2 days. **Risk:** low, behind a flag. **Touches:** sync worker, alert rules, Slack app.

## Proposed solution
\`\`\`mermaid
flowchart LR
  %% a comment
  A --> B
\`\`\`

## Phases
1. **Failure events**: the worker writes them.
2. **Alert rule**: batches per minute.

## Open questions
- Which channel with no owner? Suggested: #eng-oncall.
- Batch window?`;

test("reads title, summary line, chips, sections, phases, questions and diagrams", () => {
  const doc = parsePlan(V1);
  assert.equal(doc.title, "Slack alerts for failed syncs");
  assert.equal(doc.summary, "Failed syncs are silent. We'll alert the owning team in Slack.");
  assert.equal(doc.size, "M, about 2 days");
  assert.equal(doc.risk, "low, behind a flag");
  assert.equal(doc.touches, "sync worker, alert rules, Slack app");
  assert.deepEqual(plain(doc.sections.map((s) => [s.heading, s.id])),
    [["Proposed solution", "proposed-solution"], ["Phases", "phases"], ["Open questions", "open-questions"]]);
  assert.deepEqual(plain(doc.phases), ["Failure events: the worker writes them.", "Alert rule: batches per minute."]);
  assert.equal(doc.questions.length, 2);
  assert.equal(doc.diagrams, 1);
});

test("headings inside fenced blocks are not sections", () => {
  const doc = parsePlan("# P\n\n## Real\n````text\n## not a heading\n````\n## Also real\nx");
  assert.deepEqual(plain(doc.sections.map((s) => s.heading)), ["Real", "Also real"]);
});

test("a plan without the summary fields still reads", () => {
  const doc = parsePlan("# Quick fix\n\nBump the timeout.\n\n## Steps\n1. Change it\n2. Deploy");
  assert.equal(doc.summary, "Bump the timeout.");
  assert.equal(doc.size, null);
  assert.deepEqual(plain(doc.phases), ["Change it", "Deploy"]);
  assert.equal(parsePlan("no headings at all").title, "Plan");
});

test("duplicate headings get distinct ids", () => {
  const doc = parsePlan("# P\n## Risks\na\n## Risks\nb");
  assert.deepEqual(plain(doc.sections.map((s) => s.id)), ["risks", "risks-2"]);
});

test("diff lists added, removed and changed sections, and the change note", () => {
  const v2 = V1
    .replace("# Slack alerts for failed syncs\n", "# Slack alerts for failed syncs\n\n**Changed:** batch every 5 minutes; added How it flows.\n")
    .replace("batches per minute", "batches every 5 minutes")
    .replace("## Open questions\n- Which channel with no owner? Suggested: #eng-oncall.\n- Batch window?", "## How it flows\nWorker, rule, Slack.");
  const diff = diffPlans(V1, v2);
  assert.deepEqual(plain(diff.added), ["How it flows"]);
  assert.deepEqual(plain(diff.removed), ["Open questions"]);
  assert.deepEqual(plain(diff.changed), ["Phases"]);
  assert.equal(diff.note, "Changed: batch every 5 minutes; added How it flows.");
  assert.deepEqual(plain(diffPlans(V1, V1)), { added: [], removed: [], changed: [], note: null });
});
