const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const vm = require("node:vm");
const ts = require("typescript");
const context = vm.createContext({ exports: {}, require });
vm.runInContext(
  ts.transpileModule(
    fs.readFileSync(require("node:path").join(__dirname, "../src/lib/terminal-status.ts"), "utf8"),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } },
  ).outputText,
  context,
);
const { withTerminalStatus, terminalStatusItem, INTERRUPTED_MESSAGE } = context.exports;
const plain = (x) => JSON.parse(JSON.stringify(x));
const user = { role: "user", content: "hi" };

test("errored conversation without a final response surfaces the persisted error", () => {
  const items = withTerminalStatus([user], { status: "error", error: "The remote worker platform is not available. Reason: x", final_response: "" });
  assert.deepEqual(plain(items), [user, { role: "assistant", content: "Error: The remote worker platform is not available. Reason: x" }]);
});

test("errored conversation with a final response is left alone", () => {
  assert.deepEqual(plain(withTerminalStatus([user], { status: "error", error: "x", final_response: "done" })), [user]);
});

test("completed and running conversations get nothing appended", () => {
  for (const status of ["completed", "running", undefined]) {
    assert.equal(terminalStatusItem({ status, error: "stale" }), null);
  }
});

test("interrupted conversation explains the restart", () => {
  assert.deepEqual(plain(withTerminalStatus([user], { status: "interrupted" })), [user, { role: "assistant", content: INTERRUPTED_MESSAGE }]);
});

test("appending is idempotent across recovery polls", () => {
  const conversation = { status: "error", error: "boom", final_response: "" };
  const once = withTerminalStatus([user], conversation);
  assert.deepEqual(plain(withTerminalStatus(once, conversation)), plain(once));
});

test("missing error text falls back to a generic label", () => {
  assert.equal(terminalStatusItem({ status: "error", error: null }).content, "Error: Unknown error");
});
