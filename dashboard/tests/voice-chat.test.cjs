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
  chatAnnouncement, chatProgress, chatRunOutcome, handoffBriefing, micLevel, shouldHandOff, shouldPlayWaitingTone, taskBriefing, TONE_RESUME_QUIET_MS,
} = load("voice-chat.ts", { "./voice-dispatcher": dispatcher });
const { isVoiceClaim, otherTabWins } = load("voice-tabs.ts");

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

test("closing a chat hands voice to the board only while a result is still owed", () => {
  const off = { active: true, running: false, watchingRun: false, pendingResult: false };
  assert.equal(shouldHandOff(off), false, "nothing pending: voice just ends");
  assert.equal(shouldHandOff({ ...off, running: true }), true, "agent running");
  assert.equal(shouldHandOff({ ...off, watchingRun: true }), true, "request just handed over");
  assert.equal(shouldHandOff({ ...off, pendingResult: true }), true, "result not said yet");
  assert.equal(shouldHandOff({ ...off, active: false, running: true }), false, "voice was off");
});

test("board briefing names the task, keeps it short, and fences the old conversation", () => {
  const lines = [
    { speaker: "user", text: "check why scripbox campaign is not showing" },
    { speaker: "assistant", text: "Sure, let me check and get back to you." },
    { speaker: "user", text: "   " },
  ];
  const running = handoffBriefing("Scripbox campaign", { running: true, starting: false, lines });
  assert.match(running, /closed the task "Scripbox campaign"/);
  assert.match(running, /still working/);
  assert.match(running, /keep an eye on it/);
  assert.match(running, /as context only \(not requests to act on\)/);
  assert.match(running, /User: check why scripbox/);
  assert.match(running, /You: Sure, let me check/);
  assert.doesNotMatch(running, /User:\s*(\||$)/, "blank captions dropped");
  assert.match(handoffBriefing("X", { running: false, starting: true, lines: [] }), /still working/);
  const stopped = handoffBriefing("X", { running: false, starting: false, announcement: "Update", lines: [] });
  assert.match(stopped, /has stopped; its result follows/);
  assert.doesNotMatch(stopped, /context only/);
  const long = handoffBriefing("X", { running: true, starting: false, lines: Array.from({ length: 20 }, (_, i) => ({ speaker: "user", text: `line ${i} ` + "x".repeat(500) })) });
  assert.doesNotMatch(long, /line 13 /, "only the last six lines");
  assert.match(long, /line 19 /);
  assert.ok(long.length < 1800, "each line is capped");
});

test("a task opened from board voice always hands voice back on close", () => {
  const idle = { active: true, running: false, watchingRun: false, pendingResult: false };
  assert.equal(shouldHandOff({ ...idle, cameFromBoard: true }), true);
  assert.equal(shouldHandOff({ ...idle, cameFromBoard: false }), false);
  assert.match(handoffBriefing("X", { running: false, starting: false, announcement: null, lines: [] }), /back on the board/);
});

test("task briefing says voice is in the task and whether its agent works", () => {
  const lines = [{ speaker: "user", text: "open the billing task" }];
  const working = taskBriefing("Billing check", { running: true, lines });
  assert.match(working, /moved into the task "Billing check"/);
  assert.match(working, /is working right now/);
  assert.match(working, /context only/);
  assert.match(taskBriefing("B", { running: false, lines: [] }), /is not working right now/);
});

test("one voice session per browser: the newest tab wins, ties break by tab id", () => {
  const mine = { tab: "b", at: 1000 };
  assert.equal(otherTabWins(mine, { tab: "a", at: 1001 }), true, "newer tab takes over");
  assert.equal(otherTabWins(mine, { tab: "c", at: 999 }), false, "older tab does not");
  assert.equal(otherTabWins(mine, { tab: "b", at: 5000 }), false, "own claim ignored");
  // Both tabs see each other's claim: exactly one ends.
  for (const [a, b] of [[{ tab: "a", at: 7 }, { tab: "z", at: 7 }], [{ tab: "a", at: 7 }, { tab: "z", at: 8 }]]) {
    assert.equal(otherTabWins(a, b) !== otherTabWins(b, a), true);
  }
  assert.equal(isVoiceClaim({ type: "voice-claim", tab: "x", at: 1 }), true);
  assert.equal(isVoiceClaim({ type: "voice-claim", tab: 1, at: 1 }), false);
  assert.equal(isVoiceClaim(null), false);
});
