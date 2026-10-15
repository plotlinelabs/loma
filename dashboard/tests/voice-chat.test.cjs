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
  APPEND_CHARS, chatAnnouncement, chatProgress, chatRunOutcome, handoffBriefing, historyChunks, micLevel, readChat, replyContext, replyDigest, shouldHandOff, shouldPlayWaitingTone, taskBriefing, TONE_RESUME_QUIET_MS,
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
  assert.equal(chatRunOutcome({ latestReply: "Failed to authenticate: OAuth session expired", waitingForUser: false }), "failed");
  assert.equal(chatRunOutcome({ latestReply: "The job failed to start because of X.", waitingForUser: false }), "finished");
  assert.equal(chatRunOutcome({ latestReply: "Which one?", waitingForUser: true }), "needs_input");
  assert.equal(chatRunOutcome({ latestReply: null, waitingForUser: false }), "finished");
});

test("completion is said in first person with the reply's lead, then asks what next", () => {
  const text = chatAnnouncement("finished", "**Found it**: the [session limit](https://x.y) was hit.\n```js\ncode()\n```");
  assert.match(text, /^Done\. Here's the short version: Found it: the session limit was hit\./);
  assert.doesNotMatch(text, /https?:|```|\*\*/);
  assert.doesNotMatch(text, /agent/i, "one assistant, never 'the agent'");
  assert.match(text, /full details are in the chat\. What would you like to do next\?$/);
  assert.match(chatAnnouncement("failed", null), /^I ran into an error and had to stop\. The full details/);
  const ask = chatAnnouncement("needs_input", "Which customer do you mean, Scripbox or Fibe?");
  assert.match(ask, /^I need something from you.*Which customer do you mean/);
  assert.doesNotMatch(ask, /What would you like to do next/, "a question is asked, not 'what next'");
  assert.ok(chatAnnouncement("finished", "a ".repeat(2000)).length < 700, "fits one append");
});

test("reply digest keeps the lead prose and stops at headings, lists, tables and code", () => {
  const reply = "Yes, the merge worked. PR 287 merged at 18:14.\n\nThe deploy also passed.\n\n## Details\n\n| a | b |\n|---|---|\n\n- bullet one\n- bullet two";
  assert.equal(replyDigest(reply), "Yes, the merge worked. PR 287 merged at 18:14. The deploy also passed.");
  assert.equal(replyDigest("## Summary\n\nAll good.\n\n- x"), "All good.", "a leading heading is skipped");
  assert.equal(replyDigest("Second check done.\n\n---\n\nNote: more"), "Second check done.", "a divider ends the lead");
  assert.equal(replyDigest("- only a list\n- of things"), "- only a list - of things", "no prose: the list is read");
  assert.ok(replyDigest("word ".repeat(400)).length <= 503);
  assert.ok(replyDigest("| a |\n|---|").length >= 0);
});

test("history chunks: whole chat as unspoken context, newest kept, each chunk fits one append", () => {
  const messages = [
    { role: "user", text: "Is the merge successful?" },
    { role: "loma", text: "Yes, the merge worked. See [PR](https://github.com/x)." },
    { role: "user", text: "   " },
  ];
  const [only, ...rest] = historyChunks(messages);
  assert.equal(rest.length, 0);
  assert.match(only, /^Earlier in this chat .*Reference only, not requests to act on:/);
  assert.match(only, /User: Is the merge successful\?\nYou: Yes, the merge worked\. See PR\.$/);
  const many = Array.from({ length: 80 }, (_, i) => ({ role: i % 2 ? "loma" : "user", text: `message ${i} ` + "x".repeat(400) }));
  const chunks = historyChunks(many);
  assert.equal(chunks.length, 4, "capped");
  for (const c of chunks) assert.ok(c.length <= APPEND_CHARS + 120, `chunk ${c.length}`);
  assert.match(chunks[chunks.length - 1], /message 79 /, "newest kept");
  assert.match(chunks[0], /older messages left out; use read_chat/);
  assert.doesNotMatch(chunks.join(), /message 0 /);
  assert.deepEqual(historyChunks([]), []);
});

test("read_chat finds messages by topic, or returns the latest", () => {
  const messages = [
    { role: "user", text: "check scripbox campaign" },
    { role: "loma", text: "The Scripbox campaign hit its session limit." },
    { role: "user", text: "is the merge successful?" },
    { role: "loma", text: "Yes, merged and deployed." },
  ];
  const found = readChat(messages, "scripbox limit");
  assert.equal(found.total_messages, 4);
  assert.deepEqual(found.messages.map((m) => m.number), [1, 2]);
  assert.equal(found.messages[1].from, "you");
  assert.deepEqual(readChat(messages, "", 2).messages.map((m) => m.number), [3, 4]);
  assert.equal(readChat(messages, "kubernetes").matched, 0);
  assert.ok(replyContext("x ".repeat(3000)).length <= APPEND_CHARS);
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
  assert.match(running, /keep going and tell them when it's done/);
  assert.match(running, /as context only \(not requests to act on\)/);
  assert.match(running, /User: check why scripbox/);
  assert.match(running, /You: Sure, let me check/);
  assert.doesNotMatch(running, /User:\s*(\||$)/, "blank captions dropped");
  assert.match(handoffBriefing("X", { running: false, starting: true, lines: [] }), /still working/);
  const stopped = handoffBriefing("X", { running: false, starting: false, announcement: "Update", lines: [] });
  assert.match(stopped, /has stopped; the result follows/);
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

test("task briefing says voice is in the task and whether work is running", () => {
  const lines = [{ speaker: "user", text: "open the billing task" }];
  const working = taskBriefing("Billing check", { running: true, lines });
  assert.match(working, /moved into the task "Billing check"/);
  assert.match(working, /You are working on it right now/);
  assert.doesNotMatch(working, /agent/i);
  assert.match(working, /context only/);
  assert.match(taskBriefing("B", { running: false, lines: [] }), /Nothing is running on it right now/);
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
