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

test('org prefix shared with the owner is dropped', () => {
  assert.equal(shortRepoName('acmehq/acme-documentation'), 'docs');
  assert.equal(shortRepoName('acmehq/acme-services'), 'services');
  assert.equal(shortRepoName('acmehq/acme-sdk'), 'sdk');
  assert.equal(shortRepoName('AcmeHQ/Acme-SDK'), 'sdk');
  assert.equal(shortRepoName('acmehq/acme-website'), 'website');
});

test('other repos keep their name', () => {
  assert.equal(shortRepoName('acmelabs/tools'), 'tools');
  assert.equal(shortRepoName('acme/acme-'), 'acme-');
  assert.equal(shortRepoName('other/acme-sdk'), 'acme-sdk');
  assert.equal(shortRepoName('no-owner'), 'no-owner');
  assert.equal(shortRepoName('someone/documentation'), 'docs');
});
