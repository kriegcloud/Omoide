const { test } = require('node:test');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
const path = require('node:path');
const load = () => import(pathToFileURL(path.join(__dirname, '../src/utils/faceBox.ts')).href);
test('detector box uses upright displayed dimensions and integer detector sizing', async () => {
  const { faceBoxPercent } = await load();
  assert.deepEqual(faceBoxPercent([128, 64, 256, 128], 4000, 2000, 'detector'), { left: 10, top: 10, width: 20, height: 20 });
  // EXIF orientations 5–8 swap axes before face detection. Natural dimensions
  // of the upright thumbnail, not raw EXIF media metadata, define this space.
  for (const orientation of [5, 6, 7, 8]) {
    assert.deepEqual(faceBoxPercent([64, 128, 128, 256], 2000, 4000, 'detector'), { left: 10, top: 10, width: 20, height: 20 }, `EXIF ${orientation}`);
  }
  const odd = faceBoxPercent([0, 0, 1280, 853], 3000, 2000, 'detector');
  assert.deepEqual(odd, { left: 0, top: 0, width: 100, height: 100 });
});
test('source boxes retain editor coordinates and clip at image boundaries', async () => {
  const { faceBoxPercent } = await load();
  assert.deepEqual(faceBoxPercent([-20, 10, 60, 120], 100, 100, 'source'), { left: 0, top: 10, width: 40, height: 90 });
  assert.deepEqual(faceBoxPercent([10, 20, 30, 40], 100, 200, 'source'), { left: 10, top: 10, width: 30, height: 20 });
});
test('unknown, nonfinite, empty and out-of-image geometry cannot draw a false box', async () => {
  const { faceBoxPercent } = await load();
  for (const box of [undefined, [], [0, 0, 0, 10], [0, 0, -2, 10], [NaN, 0, 2, 2], [Infinity, 0, 2, 2], [100, 0, 2, 2]]) {
    assert.equal(faceBoxPercent(box, 100, 100, 'source'), null);
  }
  assert.equal(faceBoxPercent([1, 2, 3, 4], 0, 100, 'source'), null);
});
test('raw landscape metadata resolves to upright portrait for swapped EXIF axes', async () => {
  const { uprightFaceDimensions } = await load();
  assert.deepEqual(uprightFaceDimensions(4000, 2000, 160, 320), [2000, 4000]);
  assert.deepEqual(uprightFaceDimensions(4000, 2000, 320, 160), [4000, 2000]);
});
test('face views use one masonry tile for every detection and shared editor guide', () => {
  const fs = require('node:fs');
  const read = file => fs.readFileSync(path.join(__dirname, '../src/components', file), 'utf8');
  const detected = read('DetectedFaces.tsx');
  assert.match(detected, /<Gallery>/);
  assert.doesNotMatch(detected, /<FaceMediaGroup|groupByVideo|width: 140/);
  assert.match(read('FaceGrid.tsx'), /<Gallery>/);
  assert.match(read('FaceCard.tsx'), /<FaceBoxOverlay/);
  assert.match(read('ImageEditorDialog.tsx'), /<FaceBoxOverlay/);
});
