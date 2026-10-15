const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

function load(file, deps = {}) {
  const source = fs.readFileSync(path.join(__dirname, "../src/lib", file), "utf8");
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } });
  const mod = { exports: {} };
  new Function("module", "exports", "require", outputText)(mod, mod.exports, (id) => deps[id] ?? require(id));
  return mod.exports;
}
const dispatcher = load("voice-dispatcher.ts");
const {
  chatAnnouncement, chatProgress, chatRunOutcome, micLevel, shouldPlayWaitingTone, TONE_RESUME_QUIET_MS,
} = load("voice-chat.ts", { "./voice-dispatcher": dispatcher });

const base = { agentRunning: true, now: 100_000, lastUserSpeechAt: 0, speakingUntil: 0, voiceBusy: false };

test("tone plays only while the agent works and nobody talks", () => {
  const cases = [
    [{}, true, "agent working, quiet"],
    [{ agentRunning: false }, false, "agent idle"],
    [{ voiceBusy: true }, false, "voice is about to answer"],
    [{ speakingUntil: base.now + 1 }, false, "voice speaking"],
    [{ speakingUntil: base.now }, true, "voice just stopped"],
    [{ lastUserSpeechAt: base.now - 10 }, false, "user talking"],
    [{ lastUserSpeechAt: base.now - TONE_RESUME_QUIET_MS + 1 }, false, "user paused briefly"],
    [{ lastUserSpeechAt: base.now - TONE_RESUME_QUIET_MS }, true, "user done talking: resume"],
  ];
  for (const [overrides, expected, label] of cases) {
    assert.equal(shouldPlayWaitingTone({ ...base, ...overrides }), expected, label);
  }
});

test("mic level", () => {
  assert.equal(micLevel([]), 0);
  assert.equal(micLevel([0, 0, 0]), 0);
  assert.equal(micLevel([0.5, -0.5]), 0.5);
});

test("run outcome", () => {
  assert.equal(chatRunOutcome({ latestReply: "Found it.", waitingForUser: false }), "finished");
  assert.equal(chatRunOutcome({ latestReply: "Error: boom", waitingForUser: false }), "failed");
  assert.equal(chatRunOutcome({ latestReply: "Which one?", waitingForUser: true }), "needs_input");
  assert.equal(chatRunOutcome({ latestReply: null, waitingForUser: false }), "finished");
});

test("announcement quotes a speakable reply and points to the chat", () => {
  const text = chatAnnouncement("finished", "**Found it**: the [session limit](https://x.y) was hit.\n```js\ncode()\n```");
  assert.match(text, /^Update from the chat: the agent just finished\./);
  assert.match(text, /Found it: the session limit was hit\./);
  assert.doesNotMatch(text, /https?:|```|\*\*/);
  assert.match(text, /full answer is in the chat/);
  assert.match(chatAnnouncement("failed", null), /stopped with an error\. Give the gist/);
  assert.ok(chatAnnouncement("finished", "a ".repeat(2000)).length < 700);
});

test("progress", () => {
  const now = 50_000;
  assert.deepEqual(chatProgress({ running: true, startedAt: now - 12_400, currentStep: "running Mongodb Find", latestReply: "x", waitingForUser: false }, now),
    { running: true, elapsed_seconds: 12, current_step: "running Mongodb Find" });
  assert.equal(chatProgress({ running: true, startedAt: null, currentStep: null, latestReply: null, waitingForUser: false }, now).current_step, "thinking");
  assert.deepEqual(chatProgress({ running: false, startedAt: null, currentStep: null, latestReply: "Done.", waitingForUser: false }, now),
    { running: false, waiting_for_user: false, latest_reply: "Done." });
});
