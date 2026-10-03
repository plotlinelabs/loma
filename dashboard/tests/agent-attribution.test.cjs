const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

const context = vm.createContext({ exports: {}, require });
vm.runInContext(
  ts.transpileModule(
    fs.readFileSync(path.join(__dirname, "../src/lib/agent-attribution.ts"), "utf8"),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } },
  ).outputText,
  context,
);
const {
  buildTimeline, messageAgentName, runAgentLabel, switchLabel, blockedByToolUse,
} = context.exports;
const plain = (x) => JSON.parse(JSON.stringify(x));

test("old replies with no agent are Loma's", () => {
  assert.equal(messageAgentName({}), "Loma");
  assert.equal(messageAgentName({ agent_name: "Harry" }), "Harry");
});

test("run label names the agent and its version, or Loma", () => {
  assert.equal(runAgentLabel({ agent_id: "h", agent_name: "Harry", agent_config_version: 3 }), "Harry (v3)");
  assert.equal(runAgentLabel({ agent_id: "h", agent_name: "Harry" }), "Harry");
  assert.equal(runAgentLabel({}), "Loma");
  assert.equal(runAgentLabel(null), "Loma");
});

test("switch dividers land between the messages around them", () => {
  const messages = [
    { role: "user", content: "a", timestamp: "2026-10-02T23:18:00Z" },
    { role: "assistant", content: "b", timestamp: "2026-10-02T23:18:19Z" },
    { role: "user", content: "c", timestamp: "2026-10-02T23:20:14Z" },
    { role: "assistant", content: "d", timestamp: "2026-10-02T23:22:33Z", agent_name: "Harry" },
  ];
  const events = [{
    type: "agent_switch", from_agent_id: null, from_agent_name: "Loma", to_agent_id: "h",
    to_agent_name: "Harry", switched_by: "sam@example.com", timestamp: "2026-10-02T23:20:10Z",
  }];
  const timeline = buildTimeline(messages, events);
  assert.deepEqual(
    plain(timeline.map((i) => (i.kind === "switch" ? "switch" : i.message.content))),
    ["a", "b", "switch", "c", "d"],
  );
  assert.equal(switchLabel(events[0]), "Switched to Harry by sam@");
  assert.deepEqual(plain(buildTimeline(messages, []).map((i) => i.kind)), ["message", "message", "message", "message"]);
});

test("a switch after the last message goes at the end", () => {
  const timeline = buildTimeline(
    [{ role: "user", content: "a", timestamp: "2026-01-01T00:00:00Z" }],
    [{ type: "agent_switch", to_agent_name: "Loma", timestamp: "2026-01-02T00:00:00Z" }],
  );
  assert.equal(timeline[1].kind, "switch");
});

test("blocked calls are keyed by tool_use_id", () => {
  const map = blockedByToolUse([{ tool_use_id: "t1", target: "github" }, { target: "x" }]);
  assert.deepEqual(Object.keys(map), ["t1"]);
});

test("turn text is attributed to the reply its run saved", () => {
  const { agentForTime } = context.exports;
  const messages = [
    { role: "assistant", content: "x", timestamp: "2026-10-02T23:18:19Z" },
    { role: "assistant", content: "y", timestamp: "2026-10-02T23:22:33Z", agent_id: "h", agent_name: "Harry" },
  ];
  assert.deepEqual(plain(agentForTime(messages, "2026-10-02T23:18:10Z")), { agentId: null, agentName: "Loma" });
  assert.deepEqual(plain(agentForTime(messages, "2026-10-02T23:21:00Z")), { agentId: "h", agentName: "Harry" });
  assert.equal(agentForTime(messages, "2026-10-03T00:00:00Z"), null); // run still going
});
