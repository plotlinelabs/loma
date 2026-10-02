const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const vm = require("node:vm");
const ts = require("typescript");
const context = vm.createContext({ exports: {}, require });
vm.runInContext(
  ts.transpileModule(
    fs.readFileSync(
      require("node:path").join(__dirname, "../src/hooks/agent-scope.ts"),
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
const { AGENT_TOOL_CONFIG, agentScopeSummary, agentScopeSkills, agentScopeTools } =
  context.exports;
const plain = (x) => JSON.parse(JSON.stringify(x));

test("summary counts each domain and treats an empty one as unrestricted", () => {
  assert.equal(
    agentScopeSummary({ tools: ["gmail", "slack", "Zoho"], skills: ["a", "b"] }),
    "3 tools · 2 skills",
  );
  assert.equal(agentScopeSummary({ tools: ["gmail"], skills: ["a"] }), "1 tool · 1 skill");
  assert.equal(agentScopeSummary({ tools: [], skills: ["a"] }), "All tools · 1 skill");
  assert.equal(agentScopeSummary({ tools: ["gmail"], skills: [] }), "1 tool · All skills");
  assert.equal(agentScopeSummary({ tools: [], skills: [] }), "All tools & skills");
});
test("tools use friendly labels and keep unknown saved names", () => {
  assert.deepEqual(
    plain(agentScopeTools({ tools: ["gmail", "Zoho Books"], skills: [] }, { gmail: "Gmail" })),
    [
      { id: "gmail", name: "Gmail", description: "" },
      { id: "Zoho Books", name: "Zoho Books", description: "" },
    ],
  );
});
test("skills resolve from the catalog and fall back to the slug", () => {
  assert.deepEqual(
    plain(
      agentScopeSkills({ tools: [], skills: ["zoho-books", "hidden"] }, [
        { slug: "zoho-books", name: "Zoho Books", description: "Invoices" },
      ]),
    ),
    [
      { id: "zoho-books", name: "Zoho Books", description: "Invoices" },
      { id: "hidden", name: "hidden", description: "" },
    ],
  );
});
test("an agent chat sends explicit all-mode so saved picks do not stack", () => {
  assert.deepEqual(plain(AGENT_TOOL_CONFIG), { enabled_skills: null, enabled_tools: null });
});
