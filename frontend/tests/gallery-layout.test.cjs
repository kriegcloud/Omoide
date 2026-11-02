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
  assert.equal(galleryLayout(1920).columns, 5);
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
