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
    { text: "Here's the plan.\n\nApprove when ready.", pending: null });
});

test("while streaming, an unfinished plan is held back even past its inner fences", () => {
  const partial = "Here's the plan.\n\n````plan\n# Alerts\n```mermaid\nflowchart LR\n  A --> B\n```\n\n## Pha";
  assert.deepEqual(plain(withoutArtifactSource(partial, [], true)), { text: "Here's the plan.", pending: "plan" });
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
  assert.deepEqual(plain(withoutArtifactSource(code, [], true)), { text: code, pending: null });
});

test("unclosedFence skips closed blocks and finds the open one", () => {
  assert.equal(unclosedFence("```js\nx\n```\nok"), null);
  assert.deepEqual(plain(unclosedFence("```js\nx\n```\n````plan\n# P\n```mermaid\nA\n```\n")), { index: 12, lang: "plan" });
});
