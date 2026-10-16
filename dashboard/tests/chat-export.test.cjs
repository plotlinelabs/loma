const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

const context = vm.createContext({ exports: {} });
vm.runInContext(
  ts.transpileModule(fs.readFileSync(path.join(__dirname, "../src/lib/chat-export.ts"), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText,
  context,
);
const { conversationToMarkdown, exportFileName } = context.exports;

const chat = {
  conversation_id: "c1",
  title: "Churn summary for CS",
  started_at: "2026-10-16T06:00:00",
  messages: [
    { role: "user", content: "Summarise churn", timestamp: "2026-10-16T06:00:00" },
    { role: "assistant", content: "7 accounts churned.\n\n- Starter: 4", timestamp: "2026-10-16T06:02:00" },
    { role: "assistant", content: "   " },
    { role: "system", content: "internal" },
  ],
};

test("each message is under who said it and when, in UTC", () => {
  const md = conversationToMarkdown(chat);
  assert.equal(md, [
    "# Churn summary for CS",
    "",
    "_Started 2026-10-16 06:00 UTC. Exported from Loma._",
    "",
    "## You · 2026-10-16 06:00 UTC",
    "",
    "Summarise churn",
    "",
    "## Loma · 2026-10-16 06:02 UTC",
    "",
    "7 accounts churned.",
    "",
    "- Starter: 4",
    "",
  ].join("\n"));
});

test("shared tasks name each sender; agents keep their name", () => {
  const md = conversationToMarkdown({
    conversation_id: "c2",
    messages: [
      { role: "user", content: "a", sender: "ana@example.com" },
      { role: "user", content: "b", sender: "raj@example.com" },
      { role: "assistant", content: "c", agent_name: "Support agent" },
    ],
  });
  assert.match(md, /^# Loma chat\n/);
  assert.match(md, /## ana@example\.com\n\na\n/);
  assert.match(md, /## raj@example\.com\n\nb\n/);
  assert.match(md, /## Support agent\n\nc\n/);
});

test("a timestamp with an offset is read as given", () => {
  const md = conversationToMarkdown({ conversation_id: "c3", messages: [{ role: "user", content: "x", timestamp: "2026-10-16T11:30:00+05:30" }] });
  assert.match(md, /## You · 2026-10-16 06:00 UTC/);
});

test("file names are safe and never empty", () => {
  assert.equal(exportFileName(chat), "churn-summary-for-cs.md");
  assert.equal(exportFileName({ conversation_id: "x", title: "/// ???" }), "loma-chat.md");
  assert.equal(exportFileName({ conversation_id: "x" }), "loma-chat.md");
});
