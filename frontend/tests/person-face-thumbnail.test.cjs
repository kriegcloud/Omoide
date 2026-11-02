const { test } = require('node:test');
const assert = require('node:assert/strict');
const { hooks, load, find } = require('./detailRuntime.cjs');
test('people and hidden people profile cards use parent photo geometry and preserve person actions', () => {
  const runtime = hooks();
  const thumbnail = { src: '/parent.jpg', showsParent: true, dimensions: [2000,4000], aspectRatio: .5, onLoad() {}, onError() {} };
  const Card = load('components/PersonCard.tsx', runtime, {
    '../urlUtils': { encodeFilePath: value => value },
    '../hooks/useFaceThumbnail': { useFaceThumbnail: () => thumbnail },
    '../config': { __esModule: true, default: { PRESENTATION_MODE: false } },
  }).default;
  const person = { id: 8, name: 'Test Person', appearance_count: 4, profile_face: { thumbnail_path: 'crop.jpg', bbox: [1,2,3,4] } };
  const onSelectionClick = () => true;
  const tile = runtime.render(() => Card({ person, selecting: true, selected: true, onSelectionClick }));
  assert.equal(tile.props.href, '/person/8');
  assert.equal(tile.props.aspectRatio, .5);
  assert.equal(tile.props.selected, true);
  assert.equal(tile.props.onSelectionClick, onSelectionClick);
  assert.equal(find(tile, n => n.props?.component === 'img').props.src, '/parent.jpg');
  const overlay = find(tile, n => n.type === './FaceBoxOverlay');
  assert.deepEqual(overlay.props.bbox, [1,2,3,4]);
  assert.equal(overlay.props.sourceWidth, 2000);
  assert.equal(overlay.props.sourceHeight, 4000);
  assert.ok(find(tile, n => n.props?.children === 'Test Person'));
  assert.ok(find(tile, n => [n.props?.children].flat().includes('4 media')));
});
test('people crop fallback has a visible label and favorite menu remains available', () => {
  const runtime = hooks();
  const Card = load('components/PersonCard.tsx', runtime, {
    '../hooks/useFaceThumbnail': { useFaceThumbnail: () => ({ src: '/crop.jpg', showsParent: false, dimensions: null, aspectRatio: 1, onLoad() {}, onError() {} }) },
    '../config': { __esModule: true, default: { PRESENTATION_MODE: false } },
  }).default;
  const tile = runtime.render(() => Card({ person: { id: 8, name: 'Test', profile_face: { thumbnail_path: 'crop.jpg' } } }));
  assert.equal(tile.props.aspectRatio, 1);
  assert.equal(find(tile, n => n.type === './FaceBoxOverlay'), undefined);
  assert.ok(find(tile, n => [n.props?.children].flat().some(value => typeof value === 'string' && value.includes('Face crop'))));
  assert.ok(find(tile.props.menu, n => n.props?.['aria-label'] === 'Add to favorites'));
});
test('people without a profile face retain initials fallback without invalid geometry', () => {
  const runtime = hooks();
  const thumbnailHook = load('hooks/useFaceThumbnail.ts', runtime, {
    '../config': { API: '/api' }, '../urlUtils': { encodeFilePath: value => value },
  });
  const Card = load('components/PersonCard.tsx', runtime, {
    '../hooks/useFaceThumbnail': thumbnailHook,
    '../config': { __esModule: true, default: { PRESENTATION_MODE: true } },
  }).default;
  const tile = runtime.render(() => Card({ person: { id: 8, name: 'Test Person' } }));
  assert.equal(find(tile, n => n.props?.component === 'img'), undefined);
  assert.equal(find(tile, n => n.type === './FaceBoxOverlay'), undefined);
  assert.ok(find(tile, n => n.props?.children === 'TP'));
});
test('similar people adapt to PersonCard with parent geometry, match text and selection', () => {
  const runtime = hooks(); let toggles = 0;
  const Card = load('components/SimilarPersonCard.tsx', runtime, { '@mui/material/styles': { alpha: value => value }, '../urlUtils': { encodeFilePath: value => value }, '@mui/material': { Box: 'Box', Typography: 'Typography', Checkbox: 'Checkbox', Button: 'Button', useTheme: () => ({ palette: { common: { white: 'white' }, primary: { main: 'blue' } }, shadows: [] }) } }).default;
  const profile_face = { id: 3, media_id: 4, thumbnail_path: 'crop.jpg', media_thumbnail_path: 'parent.jpg', bbox: [1,2,3,4] };
  const tile = runtime.render(() => Card({ id: 8, name: 'Test', similarity: 98.25, thumbnail: 'legacy.jpg', profile_face, selectable: true, selected: true, onToggleSelect: () => toggles++ }));
  assert.equal(tile.type, './PersonCard');
  assert.equal(tile.props.person.profile_face, profile_face);
  assert.equal(tile.props.secondaryLabel, '98.3% match');
  assert.equal(tile.props.showFavorite, false);
  assert.equal(tile.props.selected, true);
  assert.equal(tile.props.selecting, true);
  assert.equal(tile.props.onSelectionClick(8, {}), true);
  assert.equal(toggles, 1);
});
