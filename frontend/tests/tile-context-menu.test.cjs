const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { pathToFileURL } = require('node:url');
const path = require('node:path');
const load = () => import(pathToFileURL(path.join(__dirname, '../src/components/tileContextMenu.ts')).href);
const src = (name) => fs.readFileSync(path.join(__dirname, '../src/components', name), 'utf8');
const rect = { left: 100, top: 50, width: 200 };
const el = (tag) => ({ closest: (selector) => (tag && selector.split(',').map((part) => part.trim()).includes(tag) ? {} : null) });

test('right-click opens the tile menu at the pointer', async () => {
  const { tileMenuPoint } = await load();
  assert.deepEqual(tileMenuPoint({ clientX: 140, clientY: 90, shiftKey: false, target: el(null) }, rect, true), { left: 140, top: 90 });
});

test('native menu is kept without a registered menu, on Shift, and on text inputs', async () => {
  const { tileMenuPoint } = await load();
  assert.equal(tileMenuPoint({ clientX: 140, clientY: 90, shiftKey: false, target: el(null) }, rect, false), null);
  assert.equal(tileMenuPoint({ clientX: 140, clientY: 90, shiftKey: true, target: el(null) }, rect, true), null);
  assert.equal(tileMenuPoint({ clientX: 140, clientY: 90, shiftKey: false, target: el('input') }, rect, true), null);
});

test('right-click on the tile image link still opens the tile menu', async () => {
  const { tileMenuPoint } = await load();
  assert.deepEqual(tileMenuPoint({ clientX: 140, clientY: 90, shiftKey: false, target: el('a') }, rect, true), { left: 140, top: 90 });
});

test('keyboard context menu (0,0) anchors at the tile corner', async () => {
  const { tileMenuPoint } = await load();
  assert.deepEqual(tileMenuPoint({ clientX: 0, clientY: 0, shiftKey: false, target: el(null) }, rect, true), { left: 292, top: 58 });
});

test('registry keeps one handler and unregistering a stale handler is a no-op', async () => {
  const { createTileContextMenuRegistry } = await load();
  const registry = createTileContextMenuRegistry();
  const a = () => {};
  const b = () => {};
  const offA = registry.register(a);
  registry.register(b);
  offA();
  assert.equal(registry.current(), b);
});

test('frame provides the registry and handles contextmenu; card menus register', () => {
  const frame = src('SelectableTileFrame.tsx');
  assert.match(frame, /TileContextMenuContext\.Provider/);
  assert.match(frame, /onContextMenu=/);
  for (const menu of ['MediaCardMenu.tsx', 'DatasetItemMenu.tsx']) {
    const code = src(menu);
    assert.match(code, /useTileContextMenu\(/, `${menu} registers`);
    assert.match(code, /anchorReference=/, `${menu} can open at a point`);
  }
});

test('menu wrapper does not stack a second scrim behind the round menu button', () => {
  const frame = src('SelectableTileFrame.tsx');
  const wrapper = frame.match(/\{!isSelecting && menu && \(\s*<Box([^>]*)>/);
  assert.ok(wrapper, 'menu wrapper found');
  assert.doesNotMatch(wrapper[1], /\.\.\.scrim/);
});
