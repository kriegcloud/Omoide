const { test } = require('node:test');
const assert = require('node:assert/strict');
const { hooks, load, find } = require('./detailRuntime.cjs');
function all(node, predicate) {
  if (!node || typeof node !== 'object') return [];
  return [...(predicate(node) ? [node] : []), ...[node.props?.children].flat(Infinity).flatMap(child => all(child, predicate))];
}
test('shared masonry bounds ten thousand rendered items and scrolls focus to an unmounted item', () => {
  const previous = Object.fromEntries(['window', 'ResizeObserver', 'Element', 'requestAnimationFrame', 'cancelAnimationFrame'].map(key => [key, global[key]]));
  const frames = [];
  global.window = { innerHeight: 900, addEventListener() {}, removeEventListener() {} };
  global.ResizeObserver = class { observe() {} disconnect() {} };
  global.Element = class {};
  global.requestAnimationFrame = fn => { frames.push(fn); return frames.length; };
  global.cancelAnimationFrame = () => {};
  const runtime = hooks();
  try {
    let keyHandler, focused;
    const math = load('components/galleryLayout.ts', runtime);
    const navigation = load('hotkeys/GridKeyboardNavigation.tsx', runtime, { '../hotkeys/useHotkey': { useHotkeys() {} } });
    const Gallery = load('components/Gallery.tsx', runtime, {
      './galleryLayout': math,
      '../hotkeys/GridKeyboardNavigation': navigation,
      '../hotkeys/useHotkey': { useHotkeys: (_, handler) => { keyHandler = handler; } },
    }).default;
    const scrollport = { clientWidth: 1185, scrollTop: 0, contains: () => true,
      querySelectorAll: () => [], querySelector: selector => ({ focus: () => { focused = selector; } }) };
    const items = Array.from({ length: 10000 }, (_, id) => ({ id, aspectRatio: id % 2 ? 2 : 1, content: `tile ${id}` }));
    const props = { virtualItems: items, scrollRef: { current: scrollport }, listKey: 'test' };
    const render = () => runtime.render(() => Gallery(props));
    render().ref({ clientWidth: 1200 });
    const tree = render();
    const visible = all(tree, node => node.props?.['data-virtual-item'] !== undefined);
    assert.ok(visible.length > 0 && visible.length < 40, `bounded cells: ${visible.length}`);
    const rightmost = visible.filter(node => node.props.sx.left > 800)[0];
    assert.equal(rightmost.props.sx.left + rightmost.props.sx.width, 1185, 'scrollbar width is excluded');
    const target = new global.Element();
    target.closest = selector => selector === '[data-selectable-id]' ? { dataset: { selectableId: '39' } } : null;
    keyHandler({ target, key: 'ArrowDown' });
    const updated = render();
    assert.ok(scrollport.scrollTop > 0);
    assert.ok(find(updated, node => node.props?.['data-virtual-item'] === 22));
    frames.forEach(fn => fn());
    assert.equal(focused, '[data-selectable-id="22"]');
  } finally {
    runtime.unmount();
    Object.assign(global, previous);
  }
});
