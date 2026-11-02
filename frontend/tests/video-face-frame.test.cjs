const { test } = require('node:test');
const assert = require('node:assert/strict');
const { hooks, load } = require('./detailRuntime.cjs');
function preview(face) {
  const runtime = hooks();
  const { useFaceThumbnail } = load('hooks/useFaceThumbnail.ts', runtime, {
    '../config': { API: '' }, '../urlUtils': { encodeFilePath: value => value },
    '../utils/faceBox': { uprightFaceDimensions: (w,h) => [w,h] },
  });
  return { render: () => runtime.render(() => useFaceThumbnail(face)) };
}
test('time zero is a video frame; natural decoder dimensions define box coordinates', () => {
  const p = preview({ id: 5, timestamp: 0, media_duration: 30, media_width: 1920, media_height: 1080, thumbnail_path: 'crop.jpg' });
  let view = p.render();
  assert.equal(view.src, '/api/faces/5/frame');
  assert.equal(view.dimensions, null);
  view.onLoad({ currentTarget: { naturalWidth: 1280, naturalHeight: 720 } });
  view = p.render();
  assert.deepEqual(view.dimensions, [1280,720]);
  assert.equal(view.aspectRatio, 1280/720);
  assert.equal(view.showsParent, true);
});
test('404 falls back to crop; crop errors never retry the failed frame or draw a box', () => {
  const p = preview({ id: 5, timestamp: 1.5, media_duration: 30, thumbnail_path: 'crop.jpg' });
  p.render().onError();
  let view = p.render();
  assert.equal(view.src, '/thumbnails/crop.jpg');
  view.onLoad({ currentTarget: { naturalWidth: 90, naturalHeight: 100 } });
  view = p.render();
  assert.equal(view.showsParent, false);
  assert.equal(view.dimensions, null);
  view.onError();
  assert.equal(p.render().src, '/thumbnails/crop.jpg');
});
test('legacy videos without timestamps retain labelled crops rather than a different frame', () => {
  const view = preview({ id: 5, timestamp: null, media_duration: 30, media_thumbnail_path: 'poster.jpg', thumbnail_path: 'crop.jpg' }).render();
  assert.equal(view.src, '/thumbnails/crop.jpg');
  assert.equal(view.showsParent, false);
});
test('video frame bbox stays in detector pixels without a second source-size scale', () => {
  const runtime = hooks();
  const { faceBoxPercent } = load('utils/faceBox.ts', runtime);
  assert.deepEqual(faceBoxPercent([320,180,160,90], 1280, 720, 'detector'), {
    left: 25, top: 25, width: 12.5, height: 12.5,
  });
  assert.deepEqual(faceBoxPercent([32,24,64,48], 320, 240, 'detector'), {
    left: 10, top: 10, width: 20, height: 20,
  });
});
