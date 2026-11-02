const test = require('node:test');
const assert = require('node:assert/strict');
const { runtime, load } = require('./datasetRuntime.cjs');
const api = () => load('hooks/useWindowVirtualRows.ts', runtime(), {});

test('prefix sums use measured heights and estimates, including empty lists', () => {
  const { rowOffsets } = api();
  assert.deepEqual(rowOffsets(4, 100, new Map([[1, 180], [3, 40]])), [0, 100, 280, 380, 420]);
  assert.deepEqual(rowOffsets(0, 100, new Map()), [0]);
});
test('visible range uses document list offset, window scroll and pixel overscan', () => {
  const { virtualItems } = api();
  const offsets = [0, 100, 300, 400, 600];
  assert.deepEqual(virtualItems(offsets, 450, 200, 180, 0), [{ index: 1, top: 100 }, { index: 2, top: 300 }, { index: 3, top: 400 }]);
  assert.deepEqual(virtualItems(offsets, 450, 200, 180, 200).map(x => x.index), [0, 1, 2, 3]);
  assert.deepEqual(virtualItems(offsets, 0, 1000, 180, 0), []);
  assert.deepEqual(virtualItems(offsets, 1000, 0, 180, 0), []);
  assert.deepEqual(virtualItems([0], 0, 0, 180, 100), []);
});
test('growing rows above the visible anchor compensates by their delta only', () => {
  const { anchorDelta } = api();
  assert.equal(anchorDelta([0, 100, 300, 400], [0, 160, 360, 520], 250), 60);
  assert.equal(anchorDelta([0, 100, 300, 400], [0, 100, 380, 480], 250), 0);
  assert.equal(anchorDelta([0, 100], [0, 180], -40), 0);
});
test('scrollToIndex clears the measured app header and handles tall rows and alignments', () => {
  const { indexScrollTop } = api();
  const offsets = [0, 200, 1000, 1200];
  assert.equal(indexScrollTop(offsets, 1, 'start', 300, 700, 600, 64), 436);
  assert.equal(indexScrollTop(offsets, 1, 'smart', 300, 700, 600, 64), 700);
  assert.equal(indexScrollTop(offsets, 2, 'smart', 300, 0, 600, 64), 1236);
  assert.equal(indexScrollTop(offsets, 2, 'end', 300, 0, 600, 64), 900);
});
test('hook throttles window events, compensates measured growth once and cleans up', (t) => {
  const oldWindow = global.window, oldObserver = global.ResizeObserver;
  const listeners = new Map(), frames = new Map(), observers = [];
  let nextFrame = 0, listTop = 300;
  const scrolls = [];
  global.window = {
    scrollY: 650, innerHeight: 200,
    addEventListener: (name, fn) => listeners.set(name, fn),
    removeEventListener: (name, fn) => { if (listeners.get(name) === fn) listeners.delete(name); },
    requestAnimationFrame: fn => { frames.set(++nextFrame, fn); return nextFrame; },
    cancelAnimationFrame: id => frames.delete(id),
    scrollBy: ({ top }) => { window.scrollY += top; scrolls.push(top); },
    scrollTo: ({ top }) => { window.scrollY = top; },
  };
  global.ResizeObserver = class {
    constructor(fn) { this.fn = fn; observers.push(this); }
    observe() {}
    disconnect() { this.disconnected = true; }
  };
  t.after(() => { global.window = oldWindow; global.ResizeObserver = oldObserver; });
  const rt = runtime(); rt.api.useLayoutEffect = rt.api.useEffect;
  const { useWindowVirtualRows } = load('hooks/useWindowVirtualRows.ts', rt, {});
  const containerRef = { current: { getBoundingClientRect: () => ({ top: listTop - window.scrollY }) } };
  let measuredHeights = new Map();
  let layoutKey = 'same';
  const render = () => rt.render(() => useWindowVirtualRows({ containerRef, rowCount: 10,
    estimatedHeight: 100, measuredHeights, overscan: 0, headerOffset: 64, layoutKey }));
  const firstScrollToIndex = render().scrollToIndex;
  assert.deepEqual(render().items.map(x => x.index), [3, 4, 5]);
  measuredHeights = new Map([[0, 160]]);
  assert.equal(render().totalHeight, 1060);
  assert.equal(render().scrollToIndex, firstScrollToIndex, 'measurement must not retrigger the focused-group scroll effect');
  assert.deepEqual(scrolls, [60]);
  render(); assert.deepEqual(scrolls, [60], 'does not compensate twice');
  render().scrollToIndex(7, 'start');
  assert.equal(window.scrollY, 996);
  window.scrollY = 850;
  listeners.get('scroll')(); listeners.get('scroll')(); listeners.get('resize')();
  assert.equal(frames.size, 1);
  [...frames.values()][0](); frames.clear();
  assert.deepEqual(render().items.map(x => x.index), [4, 5, 6]);
  listTop = 500; observers[0].fn();
  [...frames.values()][0](); frames.clear();
  assert.deepEqual(render().items.map(x => x.index), [2, 3, 4]);
  layoutKey = 'reordered'; measuredHeights = new Map([[0, 200]]); render();
  assert.deepEqual(scrolls, [60], 'reorders and width resets do not reuse index anchors');
  listeners.get('scroll')(); rt.unmount();
  assert.equal(frames.size, 0);
  assert.equal(listeners.size, 0);
  assert.equal(observers[0].disconnected, true);
});
