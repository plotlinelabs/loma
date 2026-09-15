const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');
function load(file, imports = {}) {
  const source = fs.readFileSync(path.join(__dirname, '../src/', file), 'utf8');
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } });
  const context = vm.createContext({ exports: {}, require: (key) => imports[key] || require(key) });
  vm.runInContext(outputText, context);
  return context.exports;
}
const meta = load('app/mcp/tool-meta.ts');
const { skillOptions, toolOptions, withSavedOptions, toggleSelection } = load('app/agents/selection-options.ts', { '@/app/mcp/tool-meta': meta });
const plain = (value) => JSON.parse(JSON.stringify(value));

test('skills preserve scopes, system subgroup, real folders and slug values', () => {
  const options = plain(skillOptions([
    { name: 'Mine', slug: 'mine', folder: 'Sales' },
    { name: 'Shared', scope: 'workspace', folder: 'Sales' },
    { name: 'Core', scope: 'system', folder: 'Engineering' },
    { name: 'Loose', scope: 'personal' },
  ]));
  assert.deepEqual(options.map(({ path }) => path), [
    ['Personal', 'Sales'], ['Organisation', 'Sales'], ['Organisation', 'System', 'Engineering'], ['Personal'],
  ]);
  assert.equal(options[0].value, 'mine');
  assert.equal(options[1].value, 'Shared');
});

test('tools reuse catalogue categories, retain saved names and group unknown providers', () => {
  const options = plain(toolOptions([
    { provider: 'github', display_name: 'GitHub', status: 'connected' },
    { provider: 'custom-mcp', display_name: 'Custom CRM', status: 'connected' },
    { provider: 'mongodb', status: 'not_connected' },
  ]));
  assert.deepEqual(options.find(({ value }) => value === 'GitHub').path, ['Organisation', 'Engineering']);
  assert.deepEqual(options.find(({ value }) => value === 'Custom CRM').path, ['Organisation', 'Other']);
  assert.deepEqual(options.find(({ value }) => value === 'gmail').path, ['Personal', 'Google']);
  assert.equal(options.length, 9);
});

test('unknown saved selections remain visible and options are deduplicated', () => {
  const item = { value: 'gmail', label: 'Gmail', path: ['Personal'] };
  const options = plain(withSavedOptions([item, item], ['gmail', 'removed', 'removed']));
  assert.equal(options.length, 2);
  assert.deepEqual(options[1].path, ['Saved selections']);
});

test('toggle preserves unrelated saved selections and empty remains all', () => {
  assert.deepEqual(plain(toggleSelection([], 'gmail')), ['gmail']);
  assert.deepEqual(plain(toggleSelection(['gmail', 'legacy'], 'gmail')), ['legacy']);
  assert.deepEqual(plain(toggleSelection(['gmail'], 'gmail')), []);
});
