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

const RFC = `# RFC: Delete option for Custom API segments

**Author** Loma · **Status** Draft, awaiting review · **Reviewers** Vinay · **PR** not raised · **Ticket** not created

Custom API segments get the same Delete action as dynamic segments, behind the same "segment in use" check.

**Decision asked:** approve that a deleted Custom API segment is not recreated by the next sync (section 10).

## 1. Problem statement
Delete only shows for CSV, Dynamic or Manual sources.

## 6. Proposed architecture
\`\`\`mermaid
flowchart LR
  D["Dashboard"] --> G["deleteCohort"]
\`\`\`

## 13. Rollout and migration
| PR | Scope | Safe alone because |
| --- | --- | --- |
| 1 | Dashboard gate | Hidden behind the source check |
| 2 | Sync guard | No behaviour change until a segment is deleted |

**Checklist**
- [ ] PR 1 merged

## 16. Open questions and follow-ups
**Open questions**
- [ ] Should a deleted segment's sync return 410? Suggested: yes.
- [ ] Notify the customer? Suggested: no.

**Follow-ups (not in this change)**
- Bulk delete.

**Sources**
- apps/dashboard/src/pages/segments/index.js`;

test("reads the RFC skeleton: meta line, decision, lead, numbered sections", () => {
  const doc = parsePlan(RFC);
  assert.equal(doc.title, "RFC: Delete option for Custom API segments");
  assert.deepEqual(plain(doc.meta), [
    { label: "Author", value: "Loma" }, { label: "Status", value: "Draft, awaiting review" },
    { label: "Reviewers", value: "Vinay" }, { label: "PR", value: "not raised" }, { label: "Ticket", value: "not created" }]);
  assert.equal(doc.decision, "approve that a deleted Custom API segment is not recreated by the next sync (section 10).");
  assert.match(doc.summary, /^Custom API segments get the same Delete action/);
  assert.deepEqual(plain(doc.sections.map((s) => s.id)),
    ["1-problem-statement", "6-proposed-architecture", "13-rollout-and-migration", "16-open-questions-and-follow-ups"]);
  assert.equal(doc.diagrams, 1);
});

test("RFC phases come from section 13's PR table; questions stop at the follow-ups", () => {
  const doc = parsePlan(RFC);
  assert.deepEqual(plain(doc.phases), ["1: Dashboard gate", "2: Sync guard"]);
  assert.deepEqual(plain(doc.questions), [
    "Should a deleted segment's sync return 410? Suggested: yes.", "Notify the customer? Suggested: no."]);
});

test("a revision's Changed line under the header is the change note", () => {
  const v2 = RFC.replace("**Decision asked:**", "**Changed:** sync returns 410 for deleted segments.\n\n**Decision asked:**");
  assert.equal(diffPlans(RFC, v2).note, "Changed: sync returns 410 for deleted segments.");
});
