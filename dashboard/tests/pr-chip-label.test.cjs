const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

// Load the real module (it only has type imports, so it transpiles standalone).
const source = fs.readFileSync(path.join(__dirname, '../src/components/tasks/taskDisplay.ts'), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } });
const mod = { exports: {} };
new Function('module', 'exports', 'require', outputText)(mod, mod.exports, require);
const { shortRepoName } = mod.exports;

test('mapped repos get their short name', () => {
  assert.equal(shortRepoName('plotlinehq/plotline-documentation'), 'docs');
  assert.equal(shortRepoName('plotlinehq/plotline-services'), 'services');
  assert.equal(shortRepoName('plotlinehq/plotline-sdk'), 'sdk');
  assert.equal(shortRepoName('PlotlineHQ/Plotline-SDK'), 'sdk');
});

test('unmapped repos drop only the plotline- prefix', () => {
  assert.equal(shortRepoName('plotlinehq/plotline-website'), 'website');
  assert.equal(shortRepoName('plotlinelabs/loma'), 'loma');
  assert.equal(shortRepoName('acme/plotline-'), 'plotline-');
  assert.equal(shortRepoName('no-owner'), 'no-owner');
});
