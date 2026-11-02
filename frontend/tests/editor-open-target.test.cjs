const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const load = () => {
  const file = path.join(__dirname, '../src/utils/editorUrlState.ts');
  const js = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const module = { exports: {} };
  new Function('require', 'module', 'exports', js)(require, module, module.exports);
  return module.exports;
};

test('grid pages open the global editor with the media id', () => {
  assert.deepEqual(load().editorOpenTarget('/images', 1666), { kind: 'patch', edit: '1666' });
});

test('the viewer for the same media opens its own editor with edit=1', () => {
  assert.deepEqual(load().editorOpenTarget('/medium/1666', 1666), { kind: 'patch', edit: '1' });
});

test('a menu for another media inside the viewer opens that media in edit mode', () => {
  assert.deepEqual(load().editorOpenTarget('/medium/1666', 42), { kind: 'navigate', to: '/medium/42?edit=1' });
});

test('MediaCardMenu routes Edit… through editorOpenTarget', () => {
  const code = fs.readFileSync(path.join(__dirname, '../src/components/MediaCardMenu.tsx'), 'utf8');
  assert.match(code, /editorOpenTarget\(location\.pathname, media\.id\)/);
});

test('closing pops the history entry the editor pushed, otherwise replaces', () => {
  const { markEditorPushed, editorCloseAction } = load();
  const pushed = markEditorPushed({ backgroundLocation: { pathname: '/images' } });
  assert.equal(pushed.backgroundLocation.pathname, '/images');
  assert.equal(editorCloseAction(pushed), 'back');
  assert.equal(editorCloseAction({ backgroundLocation: {} }), 'replace');
  assert.equal(editorCloseAction(null), 'replace');
});

test('every editor opener/closer uses the hook open/close (no pushing close)', () => {
  const read = (p) => fs.readFileSync(path.join(__dirname, '../src', p), 'utf8');
  assert.match(read('pages/MediaDetailPage.tsx'), /openEditorUrl\(\{ edit: "1" \}\) : closeEditorUrl/);
  assert.match(read('components/GridImageEditor.tsx'), /onClose=\{\(\) => close\(/);
  assert.match(read('pages/DatasetDetailPage.tsx'), /closeEditorUrl\(\{ crop: null \}\)/);
  assert.doesNotMatch(read('pages/MediaDetailPage.tsx'), /patchEditor\(\{ edit: value/);
});
