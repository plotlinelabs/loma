const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');

// Exercise the real parser without exporting an internal implementation detail.
const source = fs.readFileSync(
  path.join(__dirname, '../src/components/MarkdownContent.tsx'), 'utf8',
);
const { outputText } = ts.transpileModule(source + '\nexport { parseBlocks };', {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
});
const context = vm.createContext({ exports: {}, require });
vm.runInContext(outputText, context);
const parseScript = new vm.Script('exports.parseBlocks(input)');
function parse(input) {
  context.input = input;
  // A synchronous infinite loop cannot be stopped by a test-runner timer.
  return JSON.parse(JSON.stringify(parseScript.runInContext(context, { timeout: 500 })));
}

test('incomplete headings remain text and never stall the parser', () => {
  for (let level = 1; level <= 6; level++) {
    for (const suffix of ['', ' ', '\t']) {
      const line = '#'.repeat(level) + suffix;
      assert.deepEqual(parse(line), [{ type: 'paragraph', text: line }]);
      assert.deepEqual(parse('Before\n\n' + line + '\n\nAfter'), [
        { type: 'paragraph', text: 'Before' },
        { type: 'paragraph', text: line },
        { type: 'paragraph', text: 'After' },
      ]);
    }
  }
  assert.deepEqual(parse('Before\n## '), [{ type: 'paragraph', text: 'Before\n## ' }]);
});

test('complete headings retain all six levels', () => {
  for (let level = 1; level <= 6; level++) {
    assert.deepEqual(parse('#'.repeat(level) + ' Title'), [
      { type: 'heading', level, text: 'Title' },
    ]);
  }
  assert.deepEqual(parse('####### Title'), [{ type: 'paragraph', text: '####### Title' }]);
});

test('ordinary block formatting is preserved', () => {
  assert.deepEqual(parse('Hello\nworld\n\n## Title\n- One\n- Two\n1. First\n```js\nx\n```'), [
    { type: 'paragraph', text: 'Hello\nworld' },
    { type: 'heading', level: 2, text: 'Title' },
    { type: 'ul', items: ['One', 'Two'] },
    { type: 'ol', items: ['First'] },
    { type: 'code', lang: 'js', code: 'x' },
  ]);
  assert.deepEqual(parse('| A | B |\n| :--- | ---: |\n| a | b |'), [
    { type: 'table', headers: ['A', 'B'], alignments: ['left', 'right'], rows: [['a', 'b']] },
  ]);
  assert.deepEqual(parse(''), []);
  assert.deepEqual(parse('```js\nx'), [{ type: 'code', lang: 'js', code: 'x' }]);
});

test('every streaming prefix of mixed Markdown terminates', () => {
  const message = [
    '# Summary', 'Paragraph with **bold**, `code`, and [link](https://example.com).',
    '', '## 2. Agents', '- First', '• Second', '1. Ordered', '',
    '### Details', '| A | B |', '| :--- | ---: |', '| a | b |', '',
    '```js', 'const x = 1;', '```', '#### Four', '##### Five', '###### Six',
  ].join('\n');
  for (let i = 0; i <= message.length; i++) parse(message.slice(0, i));
});
