const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

const context = vm.createContext({ exports: {} });
vm.runInContext(
  ts.transpileModule(fs.readFileSync(path.join(__dirname, "../src/lib/artifact-fences.ts"), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText,
  context,
);
const { withoutArtifactSource, unclosedFence } = context.exports;

const PLAN = "# Alerts\n\n## How it flows\n```mermaid\nsequenceDiagram\n  A->>B: event\n```\n\n## Phases\n1. **One**: ship";
const plain = (x) => JSON.parse(JSON.stringify(x));

test("a four-backtick plan with mermaid inside is dropped whole once its card exists", () => {
  const content = `Here's the plan.\n\n\`\`\`\`plan\n${PLAN}\n\`\`\`\`\n\nApprove when ready.`;
  assert.deepEqual(plain(withoutArtifactSource(content, [{ content: PLAN }], false)),
    { text: "Here's the plan.\n\nApprove when ready.", pending: null, sections: [] });
});

test("while streaming, an unfinished plan is held back even past its inner fences", () => {
  const partial = "Here's the plan.\n\n````plan\n# Alerts\n```mermaid\nflowchart LR\n  A --> B\n```\n\n## Pha";
  assert.deepEqual(plain(withoutArtifactSource(partial, [], true)), { text: "Here's the plan.", pending: "plan", sections: ["Pha"] });
});

test("a finished plan waiting for its card is held back while streaming, shown after", () => {
  const content = `Intro\n\n\`\`\`\`plan\n${PLAN}\n\`\`\`\``;
  assert.equal(withoutArtifactSource(content, [], true).pending, "plan");
  assert.equal(withoutArtifactSource(content, [], false).text, content);
});

test("old three-backtick plans and ordinary code blocks behave as before", () => {
  const old = "```plan\n# Ship\n1. x\n```";
  assert.equal(withoutArtifactSource(old, [{ content: "# Ship\n1. x" }], false).text, "");
  const code = "Run:\n\n```bash\nls\n```";
  assert.deepEqual(plain(withoutArtifactSource(code, [], true)), { text: code, pending: null, sections: [] });
});

test("unclosedFence skips closed blocks and finds the open one", () => {
  assert.equal(unclosedFence("```js\nx\n```\nok"), null);
  assert.deepEqual(plain(unclosedFence("```js\nx\n```\n````plan\n# P\n```mermaid\nA\n```\n")), { index: 12, lang: "plan" });
});

test("the plan's sections so far are listed while it streams, skipping fenced headings", () => {
  const { planSections } = context.exports;
  const partial = "Intro\n\n````plan\n# Alerts\n\n## Proposed solution\n```mermaid\n## not a heading\n```\n\n## How it flows\nWorker";
  assert.deepEqual(plain(withoutArtifactSource(partial, [], true).sections), ["Proposed solution", "How it flows"]);
  assert.deepEqual(plain(planSections("````plan\n# T\n## **Phases**\n")), ["Phases"]);
  assert.deepEqual(plain(withoutArtifactSource("no plan", [], true).sections), []);
});
