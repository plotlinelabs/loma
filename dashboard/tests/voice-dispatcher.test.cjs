const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");
const source = fs.readFileSync(
  path.join(__dirname, "../src/lib/voice-dispatcher.ts"),
  "utf8",
);
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
});
const mod = { exports: {} };
new Function("module", "exports", "require", outputText)(
  mod,
  mod.exports,
  require,
);
const { appendTranscript, matchTasks, parseToolCall, voiceColumn, taskLabel } =
  mod.exports;
const tasks = [
  {
    conversation_id: "a",
    title: "Billing recon",
    prompt: "Check invoices",
    column: "today",
  },
  {
    conversation_id: "b",
    title: "Billing overage",
    prompt: "Check costs",
    column: "working",
  },
  {
    conversation_id: "c",
    title: "Billing recon",
    prompt: "Last month",
    column: "done",
  },
];
test("ambiguous names do not select a task", () =>
  assert.equal(matchTasks(tasks, "the billing task").length, 2));
test("exact ids can select completed tasks", () =>
  assert.equal(matchTasks(tasks, "c")[0].conversation_id, "c"));
test("live task preferred over a completed match", () =>
  assert.equal(matchTasks(tasks, "billing recon")[0].conversation_id, "a"));
test("unknown and empty references are rejected", () => {
  for (const ref of ["", "???", "missing"])
    assert.deepEqual(matchTasks(tasks, ref), []);
});
test("lane and status mapping", () => {
  for (const col of ["needs_input", "working", "done"])
    assert.equal(voiceColumn(col), col);
  for (const col of ["today", "later", "todo"])
    assert.equal(voiceColumn(col), "staged");
});
test("untitled task has a useful label", () =>
  assert.equal(
    taskLabel({ title: null, prompt: "Check invoices" }),
    "Check invoices",
  ));
test("caption fragments append, pauses and speaker changes split", () => {
  let lines = appendTranscript([], "user", "Hello", 0, 500);
  lines = appendTranscript(lines, "user", " there", 500, 1000);
  assert.equal(lines[0].text, "Hello there");
  lines = appendTranscript(lines, "assistant", " Hi", 1000, 1500);
  lines = appendTranscript(lines, "assistant", " Again", 5000, 5500);
  assert.equal(lines.length, 3);
  assert.equal(lines[2].text, "Again");
});
test("captions have bounded memory", () => {
  let lines = [];
  for (let i = 0; i < 100; i++)
    lines = appendTranscript(lines, "user", "hello", i * 4000, i * 4000 + 1);
  assert.equal(lines.length, 40);
});
function envelope(args) {
  return {
    type: "response.event",
    event: {
      type: "response.output_item.done",
      item: {
        type: "function_call",
        call_id: "call-1",
        name: "create_task",
        arguments: args,
      },
    },
  };
}
test("only completed function items produce tool calls", () => {
  assert.equal(parseToolCall({ type: "response.completed" }), null);
  assert.equal(parseToolCall(null), null);
  assert.deepEqual(parseToolCall(envelope('{"prompt":"review"}')).args, {
    prompt: "review",
  });
});
test("malformed tool args are safe objects", () => {
  for (const args of ["{bad", "null", "[]", "42"])
    assert.deepEqual(parseToolCall(envelope(args)).args, {});
});
