/* eslint-disable @typescript-eslint/no-require-imports -- Standalone Node harness. */
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const { Module } = require('node:module');
const esbuild = require('esbuild');
const filename = path.resolve(__dirname, '../../utils/editorUrlState.ts');
const loaded = new Module(filename, module);
loaded._compile(esbuild.transformSync(fs.readFileSync(filename, 'utf8'), { loader: 'ts', format: 'cjs' }).code, filename);
const { readEditorOptions, patchEditorSearch, positiveId, editorSelection } = loaded.exports;
test('editor options validate tools, guides and framing without accepting arbitrary store state', () => {
  assert.deepEqual(readEditorOptions(new URLSearchParams('tool=brightness&guides=face,bucket&compare=1&preset=portrait')), {
    tabId: 'Finetune', toolId: 'Brightness', face: true, bucket: true, compare: true, preset: 'portrait',
  });
  assert.deepEqual(readEditorOptions(new URLSearchParams('tool=garbage&guides=other&preset=bad&compare=true')), {
    tabId: 'Adjust', toolId: 'Crop', face: false, bucket: false, compare: false, preset: null,
  });
  assert.equal(editorSelection('Resize', null), 'resize');
  assert.equal(editorSelection('Adjust', 'Flip_X'), 'flip-x');
});
test('open/options/close preserve unrelated query state and close clears every editor option', () => {
  const original = new URLSearchParams('folder=a%2Fb&sort=date');
  const opened = patchEditorSearch(original, { edit: '827', tool: 'crop' });
  const changed = patchEditorSearch(opened, { guides: 'bucket,face', compare: '1', preset: 'portrait' });
  const closed = patchEditorSearch(changed, { edit: null });
  assert.equal(closed.toString(), original.toString());
  assert.equal(original.has('edit'), false);
  assert.equal(changed.get('edit'), '827');
});
test('URL identifiers reject malformed, unsafe, negative and zero ids', () => {
  for (const value of [null, '', '0', '-1', 'NaN', '1.2', '2e3', '9007199254740992']) assert.equal(positiveId(value), null);
  assert.equal(positiveId('827'), 827);
});

test('the tool schema only restores ids supported by the installed editor', () => {
  const { editorTools } = loaded.exports;
  const constants = fs.readFileSync(path.resolve(__dirname, '../../../node_modules/react-filerobot-image-editor/lib/utils/constants.js'), 'utf8');
  const toolIds = constants.match(/TOOLS_IDS=\{([^}]+)\}/)[1];
  const installed = [...toolIds.matchAll(/:"([^"]+)"/g)].map(match => match[1]);
  for (const [, tool] of Object.values(editorTools)) assert.ok(installed.includes(tool), tool);
});
test('opening a crop or pixel editor retains only one dialog owner', () => {
  assert.equal(patchEditorSearch(new URLSearchParams('edit=827'), { crop: '9' }).has('edit'), false);
  assert.equal(patchEditorSearch(new URLSearchParams('crop=9'), { edit: '827' }).has('crop'), false);
});
