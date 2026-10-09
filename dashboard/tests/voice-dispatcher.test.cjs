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
const {
  appendTranscript,
  matchTasks,
  parseToolCall,
  voiceColumn,
  taskLabel,
  planMove,
  detectFinished,
  announcement,
  speakable,
} = mod.exports;
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

const lanes = [
  { id: "today", name: "Today" },
  { id: "later", name: "Later this week" },
];
const card = (column, status = "completed", extra = {}) => ({
  conversation_id: "t",
  title: "Review PR 412",
  prompt: "",
  column,
  status,
  ...extra,
});
test("mark done follows the board's rules", () => {
  for (const column of ["working", "needs_input"])
    assert.deepEqual(planMove(card(column), "done", lanes).updates, {
      task_status: "done",
    });
  // A parked chat has run; a draft has not.
  assert.equal(planMove(card("today"), "Done", lanes).column, "done");
  assert.match(planMove(card("today", null), "done", lanes).error, /draft/);
  assert.match(planMove(card("done"), "done", lanes).error, /already/);
});
test("lanes are matched by name, id or a distinct word", () => {
  for (const to of ["Later this week", "later", "LATER", "the later lane"])
    assert.deepEqual(planMove(card("needs_input"), to, lanes).updates, {
      task_status: "todo",
      task_lane: "later",
    });
  assert.deepEqual(planMove(card("today"), "later", lanes).updates, {
    task_lane: "later",
  });
  assert.deepEqual(planMove(card("done"), "today", lanes).updates, {
    task_status: "todo",
    task_lane: "today",
  });
  const unknown = planMove(card("done"), "backlog", lanes).error;
  assert.match(unknown, /Today, Later this week, Done/);
});
test("moves the board forbids come back with a reason", () => {
  assert.match(planMove(card("working"), "today", lanes).error, /running/);
  assert.match(planMove(card("today"), "today", lanes).error, /already/);
  assert.match(planMove(card("today"), "working", lanes).error, /message/);
  assert.match(planMove(card("needs_input"), "needs_input", lanes).error, /done task/);
  assert.match(planMove(card("done"), "", lanes).error, /where/);
  assert.match(planMove(card("done", "completed", { star: {} }), "today", lanes).error, /starred/);
  assert.match(planMove(card("needs_input", "completed", { human_task: {} }), "done", lanes).error, /person/);
});
test("reopening a done task sends it to needs input", () =>
  assert.deepEqual(planMove(card("done"), "needs_input", lanes), {
    updates: { task_status: "active" },
    destination: "Needs input",
    column: "needs_input",
  }));
const seen = (id, column, status = "running") => ({
  conversation_id: id,
  title: id,
  prompt: "",
  column,
  status,
});
test("only tasks seen running are announced when they stop", () => {
  const watched = new Map();
  // First look: seeds the snapshot, announces nothing, even for old finished tasks.
  assert.deepEqual(
    detectFinished(watched, [seen("a", "working"), seen("old", "needs_input", "completed")]),
    [],
  );
  assert.deepEqual(detectFinished(watched, [seen("a", "working"), seen("old", "done")]), []);
  const out = detectFinished(watched, [seen("a", "needs_input", "completed")]);
  assert.deepEqual(out.map((t) => [t.task.conversation_id, t.outcome]), [["a", "finished"]]);
  // Told once, and tasks that left the board are forgotten.
  assert.deepEqual(detectFinished(watched, [seen("a", "needs_input", "completed")]), []);
  assert.equal(watched.has("old"), false);
});
test("how a run ended decides the wording", () => {
  const outcomes = (now) => {
    const watched = new Map([["a", "working"]]);
    return detectFinished(watched, [now]).map((t) => t.outcome);
  };
  assert.deepEqual(outcomes(seen("a", "needs_input", "error")), ["failed"]);
  assert.deepEqual(outcomes(seen("a", "needs_input", "interrupted")), ["needs_input"]);
  assert.deepEqual(outcomes(seen("a", "done", "completed")), ["done"]);
  // Parked in a lane mid-run: the user's own move.
  assert.deepEqual(outcomes(seen("a", "today", "interrupted")), []);
  assert.deepEqual(outcomes(seen("a", "working")), []);
});
test("a voice-started task that ends before the next look is still announced", () => {
  const watched = new Map([["new", "working"]]);
  assert.equal(detectFinished(watched, [seen("new", "needs_input", "completed")]).length, 1);
});
test("announcements are short, speakable and grouped", () => {
  const one = announcement([
    {
      title: "Review PR 412",
      outcome: "finished",
      reply: "**2 issues** found in `api.ts`. See [the PR](https://github.com/x/y/pull/412).\n```js\nsecret()\n```",
    },
  ]);
  assert.match(one, /"Review PR 412" just finished/);
  assert.match(one, /It says: 2 issues found in api.ts\. See the PR\. Want/);
  assert.doesNotMatch(one, /https?:|\*|`|secret/);
  assert.match(one, /Want to see it\?$/);
  const many = announcement(
    ["a", "b", "c", "d", "e"].map((title) => ({ title, outcome: "finished", reply: "long reply" })),
  );
  assert.match(many, /"a" just finished, "b" just finished, "c" just finished, and 2 more stopped/);
  assert.doesNotMatch(many, /long reply/);
  assert.equal(announcement([]), "");
  assert.match(announcement([{ title: "x", outcome: "failed" }]), /stopped with an error\. Want/);
  assert.match(announcement([{ title: "x", outcome: "finished", reply: "OK" }]), /It says: OK\. Want/);
});
test("long replies are cut at a word", () => {
  const cut = speakable("word ".repeat(200));
  assert.ok(cut.length <= 284);
  assert.match(cut, /word\.\.\.$/);
});
