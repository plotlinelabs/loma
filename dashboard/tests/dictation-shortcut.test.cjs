const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const source = fs.readFileSync(path.join(__dirname, '../src/lib/dictation-shortcut.ts'), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } });
const mod = { exports: {} };
new Function('module', 'exports', 'require', outputText)(mod, mod.exports, require);
const {
  DEFAULT_DICTATION_SHORTCUT, matchesShortcut, validateShortcut, parseShortcut,
  serializeShortcut, formatShortcut, ariaShortcut,
} = mod.exports;

const key = (code, mods = {}) => ({ code, altKey: !!mods.alt, ctrlKey: !!mods.ctrl, metaKey: !!mods.meta, shiftKey: !!mods.shift });
const sc = (code, mods = {}) => ({ code, alt: !!mods.alt, ctrl: !!mods.ctrl, meta: !!mods.meta, shift: !!mods.shift });

test('default is Option/Alt + Space and matches only that exact combo', () => {
  assert.equal(parseShortcut(null), DEFAULT_DICTATION_SHORTCUT);
  assert.ok(matchesShortcut(key('Space', { alt: true }), DEFAULT_DICTATION_SHORTCUT));
  assert.ok(!matchesShortcut(key('Space', { alt: true, shift: true }), DEFAULT_DICTATION_SHORTCUT));
  assert.ok(!matchesShortcut(key('Space'), DEFAULT_DICTATION_SHORTCUT));
  assert.ok(!matchesShortcut(key('Space', { alt: true }), null), 'off never matches');
});

test('custom shortcut round-trips through storage', () => {
  const custom = sc('KeyD', { ctrl: true, shift: true });
  const back = parseShortcut(serializeShortcut(custom));
  assert.deepEqual(back, custom);
  assert.ok(matchesShortcut(key('KeyD', { ctrl: true, shift: true }), back));
  assert.equal(parseShortcut(serializeShortcut(null)), null);
});

test('corrupt or unsafe stored values fall back to the default', () => {
  assert.equal(parseShortcut('{nope'), DEFAULT_DICTATION_SHORTCUT);
  assert.equal(parseShortcut('{"code":"KeyA"}'), DEFAULT_DICTATION_SHORTCUT);
  assert.equal(parseShortcut('42'), DEFAULT_DICTATION_SHORTCUT);
});

test('validation blocks combos that would break typing', () => {
  assert.ok(validateShortcut(sc('KeyD')), 'bare letter');
  assert.ok(validateShortcut(sc('KeyD', { shift: true })), 'shift + letter types capitals');
  assert.ok(validateShortcut(sc('KeyV', { meta: true })), 'paste');
  assert.ok(validateShortcut(sc('KeyC', { ctrl: true })), 'copy');
  assert.ok(validateShortcut(sc('AltLeft', { alt: true })), 'modifier only');
  assert.equal(validateShortcut(sc('F8')), null, 'function key alone is fine');
  assert.equal(validateShortcut(sc('KeyD', { alt: true })), null);
  assert.equal(validateShortcut(sc('KeyV', { meta: true, shift: true })), null);
});

test('labels follow the platform', () => {
  assert.equal(formatShortcut(DEFAULT_DICTATION_SHORTCUT, true), 'Option + Space');
  assert.equal(formatShortcut(DEFAULT_DICTATION_SHORTCUT, false), 'Alt + Space');
  assert.equal(formatShortcut(sc('KeyD', { meta: true, shift: true }), true), 'Shift + Cmd + D');
  assert.equal(formatShortcut(null, true), 'Off');
  assert.equal(ariaShortcut(DEFAULT_DICTATION_SHORTCUT), 'Alt+Space');
  assert.equal(ariaShortcut(sc('KeyD', { ctrl: true })), 'Control+D');
  assert.equal(ariaShortcut(null), undefined);
});
