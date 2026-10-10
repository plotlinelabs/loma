const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");
const source = fs.readFileSync(path.join(__dirname, "../src/lib/voice-viewport.ts"), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } });
const mod = { exports: {} };
new Function("module", "exports", "require", outputText)(mod, mod.exports, require);
const { planScroll, describePosition, visibleSlice, visibleMessages, readTaskScreen, scrollTaskScreen } = mod.exports;

const view = (scrollTop) => ({ scrollTop, scrollHeight: 3000, clientHeight: 1000 });

test("planScroll steps by a page or half and stops at the edges", () => {
  assert.equal(planScroll(view(1000), "down"), 1850);
  assert.equal(planScroll(view(1000), "up"), 150);
  assert.equal(planScroll(view(1000), "down", "half"), 1500);
  assert.equal(planScroll(view(1000), "up", "half"), 500);
  assert.equal(planScroll(view(1900), "down"), 2000);
  assert.equal(planScroll(view(100), "up"), 0);
  assert.equal(planScroll(view(700), "top"), 0);
  assert.equal(planScroll(view(700), "bottom"), 2000);
  assert.equal(planScroll({ scrollTop: 0, scrollHeight: 400, clientHeight: 1000 }, "down"), 0);
});

test("describePosition names the place in the thread", () => {
  assert.equal(describePosition(view(0)), "top");
  assert.equal(describePosition(view(2000)), "bottom");
  assert.equal(describePosition(view(1998)), "bottom");
  assert.equal(describePosition(view(500)), "25%");
  assert.equal(describePosition({ scrollTop: 0, scrollHeight: 400, clientHeight: 1000 }), "all");
});

test("visibleSlice keeps only the words inside the viewport", () => {
  const text = "one two three four five six seven eight nine ten";
  assert.equal(visibleSlice(text, 0, 100, 0, 100), text);
  assert.equal(visibleSlice(text, 0, 100, 200, 100), "");
  assert.equal(visibleSlice(text, 500, 100, 0, 100), "");
  const top = visibleSlice(text, 0, 100, 0, 50);
  const bottom = visibleSlice(text, 0, 100, 50, 50);
  assert.ok(text.startsWith(top) && top.length < text.length);
  assert.ok(text.endsWith(bottom) && bottom.length < text.length);
  assert.ok(!top.endsWith(" ") && !bottom.startsWith(" "));
  assert.equal(visibleSlice("  spaced \n\n out  ", 0, 10, 0, 10), "spaced out");
  assert.equal(visibleSlice("text", 0, 0, 0, 10), "");
});

test("visibleMessages lists what is in view, in order, within the budget", () => {
  const blocks = [
    { who: "user", text: "above the fold", top: 0, height: 100 },
    { who: "loma", text: "first visible reply", top: 100, height: 100 },
    { who: "user", text: "second visible ask", top: 200, height: 100 },
    { who: "loma", text: "below", top: 900, height: 100 },
  ];
  assert.deepEqual(visibleMessages(blocks, 100, 200), [
    { who: "loma", text: "first visible reply" },
    { who: "user", text: "second visible ask" },
  ]);
  const long = [{ who: "loma", text: "word ".repeat(1000), top: 0, height: 100 }];
  const [only] = visibleMessages(long, 0, 100, 200);
  assert.ok(only.text.length <= 203 && only.text.endsWith("..."));
  assert.deepEqual(visibleMessages(blocks, 5000, 100), []);
});

test("without a browser or an open task there is nothing to read or scroll", () => {
  assert.equal(readTaskScreen(), null);
  assert.equal(scrollTaskScreen("down"), null);
});
