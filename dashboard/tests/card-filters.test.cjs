const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

// Load the real module (it only has type imports, so it transpiles standalone).
const source = fs.readFileSync(path.join(__dirname, '../src/components/tasks/cardFilters.ts'), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } });
const mod = { exports: {} };
new Function('module', 'exports', 'require', outputText)(mod, mod.exports, require);
const { matchesFilters, matchFilter, liveFilters, sameViewState, EMPTY_VIEW_STATE, STAGE_FIELD, ASSIGNEE_FIELD } = mod.exports;

const fields = [
  { id: 'value', name: 'Value', type: 'number', options: [], show_on_card: true },
  { id: 'start', name: 'Pilot Start Date', type: 'date', options: [], show_on_card: true },
  { id: 'product', name: 'Product', type: 'multi_select', options: ['Nudges', 'Stories', 'Web'], show_on_card: true },
  { id: 'tier', name: 'Tier', type: 'select', options: ['A', 'B'], show_on_card: false },
  { id: 'notes', name: 'Notes', type: 'text', options: [], show_on_card: false },
  { id: 'signed', name: 'Signed', type: 'checkbox', options: [], show_on_card: false },
];
const card = (id, lane, values) => ({ card_id: id, lane, fields: values });
const acme = card('acme', 'disc', { value: 70000, start: '2026-10-12', product: ['Nudges'], tier: 'A', notes: 'Big Bank', signed: true });
const zeta = card('zeta', 'live', { value: 30000, start: '2026-09-01', product: ['Stories', 'Web'], tier: 'B' });
const blank = card('blank', 'disc', {});
const ctx = { fields, assigneesByCard: { acme: ['a@x.com'] } };
const f = (field, op, value = null) => ({ id: field + op, field, op, value });
const pass = (filters, match = 'all') => [acme, zeta, blank].filter((c) => matchesFilters(c, filters, match, ctx)).map((c) => c.card_id);

test('number conditions', () => {
  assert.deepEqual(pass([f('value', 'gt', 50000)]), ['acme']);
  assert.deepEqual(pass([f('value', 'lt', 50000)]), ['zeta']);
  assert.deepEqual(pass([f('value', 'eq', 30000)]), ['zeta']);
  assert.deepEqual(pass([f('value', 'between', [20000, 40000])]), ['zeta']);
  assert.deepEqual(pass([f('value', 'between', [null, 80000])]), ['acme', 'zeta']); // open-ended
});

test('date conditions', () => {
  assert.deepEqual(pass([f('start', 'after', '2026-10-01')]), ['acme']);
  assert.deepEqual(pass([f('start', 'before', '2026-10-01')]), ['zeta']);
  assert.deepEqual(pass([f('start', 'on', '2026-09-01')]), ['zeta']);
  assert.deepEqual(pass([f('start', 'between', ['2026-09-01', '2026-10-12'])]), ['acme', 'zeta']);
});

test('choice conditions on select, multi-select, stage and assignee', () => {
  assert.deepEqual(pass([f('product', 'any_of', ['Nudges', 'Web'])]), ['acme', 'zeta']);
  assert.deepEqual(pass([f('product', 'none_of', ['Nudges'])]), ['zeta', 'blank']);
  assert.deepEqual(pass([f('tier', 'any_of', ['B'])]), ['zeta']);
  assert.deepEqual(pass([f(STAGE_FIELD, 'any_of', ['disc'])]), ['acme', 'blank']);
  assert.deepEqual(pass([f(ASSIGNEE_FIELD, 'any_of', ['a@x.com'])]), ['acme']);
  assert.deepEqual(pass([f(ASSIGNEE_FIELD, 'empty')]), ['zeta', 'blank']);
});

test('text, checkbox and empty conditions', () => {
  assert.deepEqual(pass([f('notes', 'contains', 'bank')]), ['acme']); // case-insensitive
  assert.deepEqual(pass([f('notes', 'not_contains', 'bank')]), ['zeta', 'blank']);
  assert.deepEqual(pass([f('signed', 'checked')]), ['acme']);
  assert.deepEqual(pass([f('signed', 'not_checked')]), ['zeta', 'blank']);
  assert.deepEqual(pass([f('value', 'empty')]), ['blank']);
  assert.deepEqual(pass([f('value', 'not_empty')]), ['acme', 'zeta']);
});

test('all vs any', () => {
  const filters = [f('value', 'gt', 50000), f('tier', 'any_of', ['B'])];
  assert.deepEqual(pass(filters, 'all'), []);
  assert.deepEqual(pass(filters, 'any'), ['acme', 'zeta']);
});

test('incomplete filters and deleted fields are ignored', () => {
  assert.equal(matchFilter(acme, f('value', 'gt', null), ctx), null);
  assert.equal(matchFilter(acme, f('product', 'any_of', []), ctx), null);
  assert.equal(matchFilter(acme, f('gone', 'empty'), ctx), null);
  assert.deepEqual(pass([f('value', 'gt', null), f('gone', 'empty')]), ['acme', 'zeta', 'blank']);
  assert.deepEqual(liveFilters([f('value', 'empty'), f('gone', 'empty'), f(STAGE_FIELD, 'empty')], fields).map((x) => x.field),
    ['value', STAGE_FIELD]);
});

test('sameViewState ignores filter ids and match with one filter', () => {
  const a = { ...EMPTY_VIEW_STATE, filters: [{ id: 'x', field: 'value', op: 'gt', value: 1 }], match: 'any' };
  const b = { ...EMPTY_VIEW_STATE, filters: [{ id: 'y', field: 'value', op: 'gt', value: 1 }] };
  assert.equal(sameViewState(a, b), true);
  assert.equal(sameViewState(a, EMPTY_VIEW_STATE), false);
  assert.equal(sameViewState({ ...EMPTY_VIEW_STATE, search: ' ' }, EMPTY_VIEW_STATE), true);
});
