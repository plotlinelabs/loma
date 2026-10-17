const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

const context = vm.createContext({ exports: {} });
vm.runInContext(
  ts.transpileModule(fs.readFileSync(path.join(__dirname, "../src/lib/process-group.ts"), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText,
  context,
);
const { processIndices, processCounts, processLabel } = context.exports;

const done = { status: "done" };
const failed = { status: "error" };

test("collects every thought and step of a reply, skipping text and status", () => {
  const items = [
    { role: "user" },
    { role: "thinking" },
    { role: "steps", steps: [done] },
    { role: "assistant" },
    { role: "thinking" },
    { role: "status" },
    { role: "steps", steps: [done, done] },
    { role: "assistant" },
  ];
  assert.deepEqual([...processIndices(items, [1, 2, 3, 4, 5, 6, 7])], [1, 2, 4, 6]);
});

test("labels a finished reply by what it did", () => {
  const label = (items) => processLabel(processCounts(items));
  assert.equal(
    label([{ role: "thinking", thinkingSeconds: 3 }, { role: "steps", steps: [done, failed] }, { role: "steps", steps: [done] }]),
    "Thought and ran 3 steps · 1 failed",
  );
  assert.equal(label([{ role: "steps", steps: [done] }]), "Ran 1 step");
  assert.equal(label([{ role: "thinking", thinkingSeconds: 3 }, { role: "thinking", thinkingSeconds: 4 }]), "Thought for 7s");
  // Saved chats don't keep thinking time.
  assert.equal(label([{ role: "thinking" }, { role: "thinking", thinkingSeconds: 2 }]), "Thought process");
});

test("reports a step still running", () => {
  assert.equal(processCounts([{ role: "steps", steps: [done, { status: "running" }] }]).running, true);
  assert.equal(processCounts([{ role: "steps", steps: [done] }]).running, false);
});
