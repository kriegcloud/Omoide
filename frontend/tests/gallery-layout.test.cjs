const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const sourcePath = path.join(__dirname, '../src/components/galleryLayout.ts');
function load() {
  const module = { exports: {} };
  new Function('module', 'exports', ts.transpileModule(fs.readFileSync(sourcePath, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText)(module, module.exports);
  return module.exports;
}
test('gallery columns and four pixel gaps fill every container width', () => {
  const { galleryLayout } = load();
  for (const width of [1, 120, 320, 375, 599, 600, 899, 900, 1200, 1600, 1920]) {
    const { columns, tileWidth, gap } = galleryLayout(width);
    assert.ok(columns >= 1 && tileWidth > 0);
    assert.equal(gap, 4);
    assert.ok(Math.abs(columns * tileWidth + (columns - 1) * gap - width) < 0.00001);
  }
});
test('gallery columns respond to available content width, including side panels', () => {
  const { galleryLayout } = load();
  assert.equal(galleryLayout(320).columns, 1);
  assert.equal(galleryLayout(600).columns, 2);
  assert.equal(galleryLayout(1200).columns, 4);
  // Media tiles cap at 360 px so a 9:16 tile (640 px tall) fits the viewport.
  assert.equal(galleryLayout(1920).columns, 6);
  assert.ok(galleryLayout(2560).tileWidth <= 360);
});
test('all gallery adapters share the masonry renderer', () => {
  for (const file of ['CursorMediaGrid', 'MediaGrid', 'ReviewMediaGrid', 'MediaListPage', 'MediaAppearances', 'FaceGrid', 'DuplicateGroup', 'MediaRelatedContent']) {
    assert.match(fs.readFileSync(path.join(__dirname, `../src/components/${file}.tsx`), 'utf8'), /<Gallery[\s>]/, file);
  }
});
test('virtual masonry preserves natural aspects and fills the final column edge', () => {
  const { masonryPositions } = load();
  const { positions, height } = masonryPositions(1200, [2, 1, 0.5, 1.5, 2]);
  assert.equal(positions[0].height, positions[0].width / 2);
  assert.equal(positions[2].height, positions[2].width * 2);
  assert.equal(positions[4].top, positions[0].height + 4);
  assert.equal(positions[3].left + positions[3].width, 1200);
  assert.equal(height, Math.max(...positions.map(p => p.top + p.height)));
});

test('people thumbnail preference changes density while filling the same container', () => {
  const { galleryLayout } = load();
  const small = galleryLayout(1800, 120), medium = galleryLayout(1800, 180), large = galleryLayout(1800, 260);
  assert.ok(small.columns > medium.columns && medium.columns > large.columns);
  for (const layout of [small, medium, large]) assert.ok(Math.abs(layout.columns * layout.tileWidth + (layout.columns - 1) * layout.gap - 1800) < 0.00001);
});

test('media tiles never exceed the maximum tile width on wide containers', () => {
  const { galleryLayout, MAX_MEDIA_TILE_WIDTH } = load();
  for (const width of [900, 1600, 1920, 2560, 3840]) assert.ok(galleryLayout(width).tileWidth <= MAX_MEDIA_TILE_WIDTH, `width ${width}`);
});

test('a gallery with fewer items than columns keeps the normal tile width instead of stretching', () => {
  const { galleryLayout, galleryFit } = load();
  const full = galleryLayout(1700);
  const one = galleryFit(1700, 1);
  assert.equal(one.columns, 1);
  assert.ok(Math.abs(one.maxWidth - full.tileWidth) < 0.00001);
  const two = galleryFit(1700, 2);
  assert.ok(Math.abs(two.maxWidth - (2 * full.tileWidth + full.gap)) < 0.00001);
  const many = galleryFit(1700, 50);
  assert.equal(many.columns, full.columns);
  assert.equal(many.maxWidth, null);
});

test('Gallery constrains short masonry lists through galleryFit', () => {
  const code = fs.readFileSync(path.join(__dirname, '../src/components/Gallery.tsx'), 'utf8');
  assert.match(code, /galleryFit\(/);
});

test('deleting a tile only moves tiles below it in the same column (no grid reshuffle)', () => {
  const { assignColumns } = load();
  const keys = Array.from({ length: 20 }, (_, i) => `m${i}`);
  const first = assignColumns(keys, 4, null);
  const after = assignColumns(keys.filter(k => k !== 'm5'), 4, first);
  for (const [key, column] of after) assert.equal(column, first.get(key), `${key} stayed in its column`);
});

test('new tiles fill the shortest column and a column change re-deals', () => {
  const { assignColumns } = load();
  const first = assignColumns(['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'], 4, null);
  const removed = assignColumns(['a', 'c', 'd', 'e', 'f', 'g', 'h'], 4, first);
  const added = assignColumns(['a', 'c', 'd', 'e', 'f', 'g', 'h', 'i'], 4, removed);
  assert.equal(added.get('i'), first.get('b'), 'the gap left by b is filled first');
  const resized = assignColumns(['a', 'c', 'd', 'e', 'f', 'g', 'h', 'i'], 3, null);
  assert.deepEqual([...resized.values()], [0, 1, 2, 0, 1, 2, 0, 1]);
});

test('heavy drift rebalances instead of leaving one column nearly empty', () => {
  const { assignColumns } = load();
  const keys = Array.from({ length: 24 }, (_, i) => `k${i}`);
  const first = assignColumns(keys, 3, null);
  const drained = keys.filter(k => first.get(k) !== 0 || k === 'k0');
  const next = assignColumns(drained, 3, first);
  const sizes = [0, 0, 0]; for (const c of next.values()) sizes[c]++;
  assert.ok(Math.max(...sizes) - Math.min(...sizes) <= 1, `balanced: ${sizes}`);
});
