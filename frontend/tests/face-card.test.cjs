const { test } = require('node:test');
const assert = require('node:assert/strict');
const { hooks, load, find } = require('./detailRuntime.cjs');
function card(face) {
  const runtime = hooks(); const navigations = [];
  const thumbnailModules = {
    '../config': { API: '/api' },
    '../urlUtils': { encodeFilePath: value => value },
    '../utils/faceBox': { uprightFaceDimensions: (w,h,nw,nh) => (w > h) !== (nw > nh) ? [h,w] : [w,h] },
  };
  const thumbnailHook = load('hooks/useFaceThumbnail.ts', runtime, thumbnailModules);
  const Card = load('components/FaceCard.tsx', runtime, {
    '../hooks/useFaceThumbnail': thumbnailHook,
    '@mui/material': { Box: 'Box', Tooltip: 'Tooltip', IconButton: 'IconButton', Typography: 'Typography', useTheme: () => ({ palette: { primary: { main: 'blue' } } }) },
    'react-router-dom': { useNavigate: () => (...args) => navigations.push(args), useLocation: () => ({ pathname: '/person/1' }) },
    '../urlUtils': { encodeFilePath: value => value },
    '../utils/faceBox': { uprightFaceDimensions: (w,h,nw,nh) => (w > h) !== (nw > nh) ? [h,w] : [w,h] },
  }).default.type;
  const props = { face, isProfile: false, selected: true, selecting: true, onSelectionClick: () => true };
  return { render: () => runtime.render(() => Card(props)), navigations };
}
test('video exact frame keeps timestamp navigation and selection on the shared tile frame', () => {
  const c = card({ id: 3, media_id: 7, thumbnail_path: 'crop.jpg', timestamp: 12.5, media_thumbnail_path: 'other-frame.jpg', media_duration: 30 });
  const tile = c.render();
  assert.equal(tile.type, './SelectableTileFrame');
  assert.equal(tile.props.selected, true);
  assert.equal(tile.props.selecting, true);
  assert.equal(find(tile, n => n.props?.component === 'img').props.src, '/api/api/faces/3/frame');
  assert.equal(find(tile, n => n.type === './FaceBoxOverlay'), undefined);
  find(tile, n => n.props?.component === 'img').props.onLoad({ currentTarget: { naturalWidth: 1280, naturalHeight: 720 } });
  const loaded = c.render();
  const overlay = find(loaded, n => n.type === './FaceBoxOverlay');
  assert.equal(overlay.props.sourceWidth, 1280);
  assert.equal(overlay.props.sourceHeight, 720);
  find(loaded, n => n.props?.component === 'img').props.onError();
  const fallback = c.render();
  assert.equal(find(fallback, n => n.props?.component === 'img').props.src, '/api/thumbnails/crop.jpg');
  assert.equal(find(fallback, n => n.type === './FaceBoxOverlay'), undefined);
  tile.props.onOpen();
  assert.equal(c.navigations[0][0], '/medium/7');
  assert.equal(c.navigations[0][1].state.sceneStart, 12.5);
  assert.equal(c.navigations[0][1].state.autoplayVideo, true);
});
test('upright photo parent controls natural aspect and rectangle coordinates; failed parent falls back without a false box', () => {
  const c = card({ id: 3, media_id: 7, thumbnail_path: 'crop.jpg', bbox: [64,128,128,256], media_thumbnail_path: 'parent.jpg', media_width: 4000, media_height: 2000 });
  let tile = c.render();
  find(tile, n => n.props?.component === 'img').props.onLoad({ currentTarget: { naturalWidth: 160, naturalHeight: 320 } });
  tile = c.render();
  assert.equal(tile.props.aspectRatio, .5);
  const overlay = find(tile, n => n.type === './FaceBoxOverlay');
  assert.equal(overlay.props.sourceWidth, 2000);
  assert.equal(overlay.props.sourceHeight, 4000);
  assert.equal(overlay.props.space, 'detector');
  find(tile, n => n.props?.component === 'img').props.onError();
  tile = c.render();
  assert.equal(find(tile, n => n.props?.component === 'img').props.src, '/api/thumbnails/crop.jpg');
  assert.equal(find(tile, n => n.type === './FaceBoxOverlay'), undefined);
});
