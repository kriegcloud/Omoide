/* eslint-disable @typescript-eslint/no-require-imports -- Standalone Node regression harness; no frontend test runner is configured. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { Module } = require('node:module');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const esbuild = require('esbuild');
const ts = require('typescript');

const frontend = path.resolve(__dirname, '../../..');

function loadTypeScript(relativePath) {
  const filename = path.join(frontend, relativePath);
  const compiled = new Module(filename, module);
  compiled.paths = Module._nodeModulePaths(frontend);
  compiled._compile(esbuild.transformSync(fs.readFileSync(filename, 'utf8'), {
    loader: 'ts', format: 'cjs',
  }).code, filename);
  return compiled.exports;
}

// Exercise the real editor initialization without mounting its canvas or
// loading personal media. This project has no browser/React test runner.
const editorFilename = path.join(frontend, 'src/components/ImageEditorDialog.tsx');
const editor = ts.createSourceFile(editorFilename, fs.readFileSync(editorFilename, 'utf8'),
  ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let initialStateFactory;
function visit(node) {
  if (ts.isVariableDeclaration(node) && node.name.getText(editor) === 'initialDesignState') {
    initialStateFactory = node.initializer.arguments[0].getText(editor);
  }
  ts.forEachChild(node, visit);
}
visit(editor);
assert.ok(initialStateFactory, 'ImageEditorDialog must initialize its design state');
const initialization = esbuild.transformSync(`(${initialStateFactory})()`, { loader: 'ts' }).code;
const { designStateToOps } = loadTypeScript('src/utils/editorOps.ts');
const historical = { adjustments: { rotation: 90, crop: { x: 20, y: 10, width: 400, height: 300 } } };
const editedMedia = { id: 7, width: 600, height: 800, edit_design_state: historical };

function initialState(mode, loadableDesignState) {
  return vm.runInNewContext(initialization, { mode, loadableDesignState, media: editedMedia });
}

test('reopening overwritten pixels does not replay the previous crop or rotation', () => {
  const restored = initialState('write', undefined);
  assert.equal(restored, undefined);
  assert.deepEqual(designStateToOps(restored ?? {}, 600, 800), []);
  assert.equal(editedMedia.edit_design_state, historical, 'historical design metadata is preserved');
});

test('write mode always starts neutral even when an explicit preset is supplied', () => {
  assert.equal(initialState('write', historical), undefined);
});

test('virtual editing restores the explicit design for the unchanged source', () => {
  const restored = initialState('virtual', historical);
  assert.equal(restored, historical);
  assert.deepEqual(designStateToOps(restored, 600, 800), [
    { op: 'crop', x: 20, y: 10, width: 400, height: 300 },
    { op: 'rotate', degrees: 90 },
  ]);
});

test('a new virtual crop never falls back to history already baked into the media', () => {
  assert.equal(initialState('virtual', null), undefined);
});

function luminance(color) {
  const channels = color.slice(1).match(/../g)
    .map(value => parseInt(value, 16) / 255)
    .map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
}

function contrast(foreground, background) {
  const first = luminance(foreground);
  const second = luminance(background);
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
}

const { getTheme } = loadTypeScript('src/theme.ts');
for (const mode of ['light', 'dark']) {
  for (const variant of ['filled', 'outlined']) {
    test(`${mode} ${variant} info chips meet 4.5:1 text contrast`, context => {
      const theme = getTheme(mode);
      const style = theme.components.MuiChip.styleOverrides.colorInfo({ theme, ownerState: { variant } });
      const ratio = contrast(style.color, style.background);
      context.diagnostic(`${style.color} on ${style.background}: ${ratio.toFixed(2)}:1`);
      assert.ok(ratio >= 4.5, `${style.color} on ${style.background}: ${ratio.toFixed(2)}:1`);
      for (const [selector, stateStyle] of Object.entries(style)) {
        if (!selector.startsWith('&') || !selector.includes('MuiChip-clickable')) continue;
        const stateRatio = contrast(stateStyle.color ?? style.color, stateStyle.background ?? style.background);
        assert.ok(stateRatio >= 4.5, `${selector}: ${stateRatio.toFixed(2)}:1`);
      }
    });
  }
}
