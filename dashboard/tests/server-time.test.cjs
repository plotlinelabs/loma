const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const source = fs.readFileSync(path.join(__dirname, '../src/lib/server-time.ts'), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } });
const mod = { exports: {} };
new Function('module', 'exports', 'require', outputText)(mod, mod.exports, require);
const { parseServerTime, isDeadRun } = mod.exports;

// The API's format: UTC, microseconds, no offset (Python isoformat of a naive datetime).
const naive = (ms) => new Date(ms).toISOString().replace('Z', '000');
const NOW = Date.parse('2026-10-14T12:36:00Z');

test('runs in a non-UTC zone, where the bug showed', () => {
  assert.notEqual(new Date(NOW).getTimezoneOffset(), 0, 'run with TZ=Asia/Kolkata');
});

test('offset-less backend timestamps are read as UTC', () => {
  assert.equal(parseServerTime('2026-10-14T12:36:00.123000'), Date.parse('2026-10-14T12:36:00.123Z'));
  assert.equal(parseServerTime('2026-10-14T12:36:00Z'), Date.parse('2026-10-14T12:36:00Z'));
  assert.equal(parseServerTime('2026-10-14T18:06:00+05:30'), Date.parse('2026-10-14T12:36:00Z'));
  assert.ok(Number.isNaN(parseServerTime(null)));
  assert.ok(Number.isNaN(parseServerTime('')));
});

test('a live run with a fresh heartbeat is not dead', () => {
  assert.equal(isDeadRun('running', naive(NOW - 10_000), NOW), false);
  assert.equal(isDeadRun('running', naive(NOW - 4 * 60_000), NOW), false);
});

test('a running conversation silent for over 5 minutes is dead', () => {
  assert.equal(isDeadRun('running', naive(NOW - 6 * 60_000), NOW), true);
  assert.equal(isDeadRun('running', naive(NOW - 3 * 3600_000), NOW), true);
});

test('finished, queued or heartbeat-less conversations are never called dead', () => {
  assert.equal(isDeadRun('completed', naive(NOW - 3 * 3600_000), NOW), false);
  assert.equal(isDeadRun('queued', naive(NOW - 3 * 3600_000), NOW), false);
  assert.equal(isDeadRun('running', null, NOW), false);
});
