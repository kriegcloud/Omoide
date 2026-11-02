const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const { hooks, load, find } = require('./detailRuntime.cjs');

test('MediaGrid uses windowed masonry with complete selection order and matching image geometry', () => {
  const rt = hooks(); let selectionOptions;
  const MediaGrid = load('components/MediaGrid.tsx', rt, {
    '../stores/useListStore': { useListStore: fn => fn({ lists: { images: { hasMore: true } } }) },
    '../context/SelectionContext': { useSelection: () => ({ selectedIds: new Set(), setSelected() {} }) },
    '../hooks/useMarqueeSelection': { useGridSelection: options => { selectionOptions = options; return { onItemClick() {} }; } },
  }).MediaGrid;
  const items = Array.from({ length: 10000 }, (_, id) => ({ id, width: 100, height: id % 2 ? 300 : 10 }));
  const tree = rt.render(() => MediaGrid({ mediaItems: items, listKey: 'images' }));
  const gallery = find(tree, node => node.type === './Gallery');
  assert.equal(gallery.props.virtualItems.length, 10000);
  assert.deepEqual(selectionOptions.orderedIds, items.map(item => item.id));
  assert.equal(gallery.props.virtualItems[0].aspectRatio, 1 / .65);
  assert.equal(gallery.props.virtualItems[1].aspectRatio, 1 / 1.5);
  assert.equal(gallery.props.virtualItems[9].content.props.onSelectionClick, gallery.props.virtualItems[0].content.props.onSelectionClick);
  assert.equal(gallery.props.listKey, 'images');
  rt.unmount();
});

test('ReviewMediaGrid exposes windowed items and guarded internal paging without replacing legacy children', () => {
  const rt = hooks();
  const Review = load('components/ReviewMediaGrid.tsx', rt).default;
  const virtualItems = [{ id: 8, aspectRatio: 1, content: React.createElement('tile') }];
  let loads = 0;
  const props = { itemCount: 1, isLoading: false, hasMore: true, loaderRef() {}, empty: 'empty',
    children: 'legacy', virtualItems, listKey: 'review:folder', onEndReached: () => loads++ };
  let tree = rt.render(() => Review(props));
  let gallery = find(tree, node => node.type === './Gallery');
  assert.equal(gallery.props.virtualItems, virtualItems);
  assert.equal(gallery.props.listKey, props.listKey);
  gallery.props.onEndReached(); assert.equal(loads, 1);
  tree = rt.render(() => Review({ ...props, isLoading: true }));
  find(tree, node => node.type === './Gallery').props.onEndReached(); assert.equal(loads, 1);
  tree = rt.render(() => Review({ ...props, error: 'offline' }));
  find(tree, node => node.type === './Gallery').props.onEndReached(); assert.equal(loads, 1);
  tree = rt.render(() => Review({ ...props, virtualItems: undefined }));
  gallery = find(tree, node => node.type === './Gallery');
  assert.equal(gallery.props.children, 'legacy');
  rt.unmount();
});

test('duplicate thumbnails defer loading and decode asynchronously', () => {
  const rt = hooks();
  const Duplicate = load('components/DuplicateMediaCard.tsx', rt, { '../formatUtils': { formatBytes: () => '1 KB' }, '@mui/material': new Proxy({ useTheme: () => ({ shadows: ['', 'shadow'], palette: { primary: { main: 'primary' } } }) }, { get: (target, key) => target[key] ?? key }) }).DuplicateMediaCard;
  const duplicate = rt.render(() => Duplicate({ media: { id: 8, filename: 'fixture.jpg' } }));
  const image = find(duplicate, node => node.props?.component === 'img');
  assert.equal(image.props.loading, 'lazy');
  assert.equal(image.props.decoding, 'async');
  rt.unmount();
});

test('face thumbnails decode asynchronously', () => {
  const faceRt = hooks();
  const Face = load('components/FaceCard.tsx', faceRt, {
    'react-router-dom': { useNavigate: () => () => {} },
    '../hooks/useFaceThumbnail': { useFaceThumbnail: () => ({ src: 'fixture.jpg', aspectRatio: 1 }) },
  }).default.type;
  const face = faceRt.render(() => Face({ face: { id: 9 }, isProfile: false }));
  const faceImage = find(face, node => node.props?.component === 'img');
  assert.equal(faceImage.props.loading, 'lazy');
  assert.equal(faceImage.props.decoding, 'async');
  faceRt.unmount();
});

test('cursor integration renderer reuses the caller selection handler without owning another selection scope', () => {
  const rt = hooks();
  const { WindowedMediaGallery } = load('components/MediaGrid.tsx', rt, {
    '../context/SelectionContext': { useSelection() { throw Error('caller owns selection'); } },
    '../hooks/useMarqueeSelection': { useGridSelection() { throw Error('caller owns selection'); } },
  });
  const onSelectionClick = () => true;
  const onEndReached = () => {};
  const tree = rt.render(() => WindowedMediaGallery({ mediaItems: [{ id: 3 }], listKey: 'cursor', onSelectionClick, onEndReached }));
  assert.equal(tree.props.virtualItems[0].content.props.onSelectionClick, onSelectionClick);
  assert.equal(tree.props.onEndReached, onEndReached);
  rt.unmount();
});
