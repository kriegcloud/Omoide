const { test } = require('node:test');
const assert = require('node:assert/strict');
const { hooks, load, find, settle, noop } = require('./detailRuntime.cjs');

function dialog(detail) {
  global.window = global.window || { addEventListener() {}, removeEventListener() {}, setTimeout, clearTimeout };
  const runtime = hooks();
  const calls = [];
  const Dialog = load('components/watermarks/WatermarkReviewDialog.tsx', runtime, {
    'react-router-dom': { useSearchParams: () => [new URLSearchParams(), noop] },
    '../../config': { API: '' },
    '../../urlUtils': { encodeFilePath: path => path.replace(/\\/g, '/').split('/').map(encodeURIComponent).join('/') },
    '../../hotkeys/useHotkey': { useDialogHotkeyScope: () => ({ current: null }), useHotkey: noop, useHotkeys: noop },
    '../../services/watermarkOperations': {
      watermarkJson: async url => { calls.push(url); return url.startsWith('/api/watermarks/') ? { id: 44, media_id: 45, status: 'proposed', regions: [], preview_path: 'p.jpg' } : detail; },
      watermarkWrite: async () => ({}), jsonBody: x => x,
    },
    './WatermarkMaskEditor': { __esModule: true, default: function WatermarkMaskEditor() { return null; } },
    './WatermarkComparison': { __esModule: true, default: function WatermarkComparison() { return null; } },
  }).default;
  const render = () => runtime.render(() => Dialog({ id: 44, onClose: noop, onStep: noop, onAction: noop }));
  return { render, calls };
}

test('review dialog reads the file path from the media detail envelope (no TypeError)', async () => {
  const view = dialog({ media: { id: 45, path: 'T7/characters/a b.jpg' }, persons: [], faces: [] });
  view.render(); await settle();
  const tree = view.render();
  const comparison = find(tree, node => node.type?.name === 'WatermarkComparison');
  assert.ok(comparison, 'comparison renders instead of an error');
  assert.match(comparison.props.original, /\/originals\/T7\/characters\/a%20b\.jpg$/);
});

test('a media detail without a path shows a readable error, not a TypeError', async () => {
  const view = dialog({ media: {} });
  view.render(); await settle();
  const alert = find(view.render(), node => node.props?.severity === 'error');
  assert.match(String(alert?.props.children), /path is unavailable/);
});
