// Face/bucket guides must follow filerobot zoom + pan (user bug 2026-11-02).
// Numbers mirror the live editor: 3024x4032 photo, stage-space image
// 2950x3933, canvasScale ~0.1, Konva stage at (129,145) in a 1339x411 stage.
const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const fs = require('node:fs');
const ts = require(path.join(__dirname, '../node_modules/typescript'));
const src = fs.readFileSync(path.join(__dirname, '../src/utils/editorOverlay.ts'), 'utf8');
const js = ts.transpileModule(src, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const mod = { exports: {} };
new Function('module', 'exports', js)(mod, mod.exports);
const { measureEditorImage, sameOverlay } = mod.exports;

const host = { left: 113, top: 129, width: 1382, height: 443 };
const stage = { left: 129, top: 145, width: 1339, height: 411 };
const shown = { x: 5220, y: 75, abstractX: 5220, abstractY: 75, width: 2950, height: 3933 };
const base = { canvasScale: 0.1, shownImageDimensions: shown, adjustments: { rotation: 0, crop: {} } };
const r = (o) => [o.left, o.top, o.width, o.height].map((v) => Math.round(v));

test('fit view: stage-space dims are scaled by canvasScale (old code used them as px)', () => {
  const out = measureEditorImage({ ...base, toolId: 'Brightness' }, host, stage);
  assert.deepStrictEqual(r(out), [16 + 522, 16 + 8, 295, 393]);
});

test('zoom 2x panned: rect doubles and shifts by the pan', () => {
  const out = measureEditorImage({ ...base, toolId: 'Brightness', zoom: { factor: 2, x: -400, y: -100 } }, host, stage);
  assert.deepStrictEqual(r(out), [16 - 400 + 1044, 16 - 100 + 15, 590, 787]);
  // A face at 25% across stays on the same image pixel.
  const fit = measureEditorImage({ ...base, toolId: 'Brightness' }, host, stage);
  assert.ok(!sameOverlay(fit, out));
});

test('crop tool ignores zoom and uses the abstract position', () => {
  const out = measureEditorImage({ ...base, toolId: 'Crop', zoom: { factor: 3, x: -900, y: -900 } }, host, stage);
  assert.deepStrictEqual(r(out), [16 + 522, 16 + 8, 295, 393]);
});

test('non-crop tool re-centres an applied crop', () => {
  const crop = { x: 0, y: 0, width: 1475, height: 1966 }; // top-left quarter
  const out = measureEditorImage({ ...base, toolId: 'Brightness', adjustments: { rotation: 0, crop } }, host, stage);
  // image shifts right/down by half the uncropped remainder so the crop is centred
  assert.deepStrictEqual(r(out), [Math.round(16 + (5220 + 737.5) * 0.1), Math.round(16 + (75 + 983.5) * 0.1), 295, 393]);
});

test('missing stage or dimensions yields no overlay', () => {
  assert.strictEqual(measureEditorImage(base, host, null), null);
  assert.strictEqual(measureEditorImage({ canvasScale: 1 }, host, stage), null);
});

test('sameOverlay ignores sub-pixel jitter', () => {
  const a = { left: 1, top: 2, width: 3, height: 4, rotation: 0, clip: null };
  assert.ok(sameOverlay(a, { ...a, left: 1.2 }));
  assert.ok(!sameOverlay(a, { ...a, width: 4 }));
});
