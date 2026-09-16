const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const vm = require("node:vm");
const ts = require("typescript");
const context = vm.createContext({ exports: {}, require });
vm.runInContext(
  ts.transpileModule(
    fs.readFileSync(
      require("node:path").join(__dirname, "../src/hooks/picker-selection.ts"),
      "utf8",
    ),
    {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2020,
      },
    },
  ).outputText,
  context,
);
const { setItems, checkState, pickerItems } = context.exports;
const plain = (x) => JSON.parse(JSON.stringify(x));
const tools = [
  { id: "Bash", name: "Bash", group: "built-in" },
  { id: "Read", name: "Read", group: "built-in" },
  { id: "WebSearch", name: "Web search", group: "built-in" },
];
test("bulk clear protects required tools and preserves unavailable saved IDs", () => {
  assert.deepEqual(
    plain(
      setItems(
        ["Bash", "WebSearch", "gone"],
        tools.map((t) => t.id),
        ["Bash", "WebSearch"],
        false,
        ["Bash", "Read"],
      ),
    ),
    ["gone", "Bash", "Read"],
  );
});
test("null all becomes explicit; filtered changes do not affect hidden items", () => {
  assert.deepEqual(plain(setItems(null, ["a", "b", "c"], ["b"], false)), [
    "a",
    "c",
  ]);
  assert.deepEqual(plain(setItems([], ["a", "b"], ["a"], true)), ["a"]);
});
test("group tri-state ignores required descendants", () => {
  const items = pickerItems(tools, [], "tools");
  assert.equal(checkState(items, ["Bash", "Read"]), false);
  assert.equal(checkState(items, null), true);
  assert.equal(checkState(items.slice(0, 2), []), true);
  const skills = pickerItems(
    [],
    [
      { slug: "a", name: "A" },
      { slug: "b", name: "B" },
    ],
    "skills",
  );
  assert.equal(checkState(skills, ["a"]), "mixed");
});
test("scope, folder, tags and descriptions remain searchable and distinct", () => {
  const items = pickerItems(
    [],
    [
      {
        slug: "a",
        name: "A",
        scope: "personal",
        folder: "Support",
        tags: ["debug"],
      },
      { slug: "b", name: "B", scope: "workspace", folder: "Support" },
    ],
    "skills",
  );
  assert.deepEqual(plain(items.map((i) => i.path)), [
    ["Personal", "Support"],
    ["Workspace", "Support"],
  ]);
  assert.ok(items[0].search.includes("debug"));
});
