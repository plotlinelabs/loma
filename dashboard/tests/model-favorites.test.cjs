const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const scope = vm.createContext({ exports: {}, require: () => ({}) });
vm.runInContext(ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/hooks/useAgentModels.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText, scope);
const { FAVORITE_MODEL_IDS: ids, FAVORITE_MODEL_LABELS: labels, favoriteModelRank, isFavoriteModel } = scope.exports;
test('exact favorite order, use-case labels and provider-specific matching', () => {
  assert.deepEqual(Array.from(ids), ['anthropic/claude-opus-5-5', 'codex/gpt-6-sol', 'codex/gpt-6-astra']);
  assert.deepEqual(ids.map(id => labels[id]).join('|'), 'Claude-Opus-5.5 (For Coding)|GPT-6-Sol (For Writing)|GPT-6-Astra (For Complex Tasks)');
  ids.forEach((id, rank) => { assert.equal(favoriteModelRank({ id }), rank); assert.equal(isFavoriteModel({ id }), true); });
  ['codex/gpt-5.6-sol', 'anthropic/claude-fable-5-1', 'opencode-go/glm-5.3-flash', 'openai/gpt-6-sol'].forEach(id => {
    assert.equal(favoriteModelRank({ id }), null);
    assert.equal(isFavoriteModel({ id }), false);
  });
});
