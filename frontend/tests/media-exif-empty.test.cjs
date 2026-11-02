const { test } = require('node:test');
const assert = require('node:assert/strict');
const { hooks, load, find, settle, noop } = require('./detailRuntime.cjs');

function exifPanel(body) {
  const runtime = hooks();
  const { MediaExif } = load('components/MediaExif.tsx', runtime, {
    '../services/exif': { getExifData: async () => body },
    '../context/UndoContext': { useMutationRefresh: noop },
    './ListState': { __esModule: true, default: function ListState() { return null; } },
  });
  return () => runtime.render(() => MediaExif({ mediaId: 1 }));
}
const nulls = { id: 7, media_id: 1, make: null, model: null, timestamp: null, exposure_time: null, focal_length: null, iso: null, aperture: null, lat: null, lon: null, city: null, country: null };

test('an extraction with no displayable values shows the empty state, not a blank panel (R9-02)', async () => {
  const render = exifPanel(nulls); render(); await settle();
  const state = find(render(), node => node.type?.name === 'ListState');
  assert.ok(state, 'ListState rendered');
  assert.equal(state.props.empty, true);
  assert.match(state.props.emptyMessage, /No EXIF data/);
});

test('model-only camera and a zero ISO are shown', async () => {
  const render = exifPanel({ ...nulls, model: 'iPhone 15', iso: 0 }); render(); await settle();
  const text = JSON.stringify(render());
  assert.match(text, /iPhone 15/);
  assert.match(text, /"ISO"/);
});
