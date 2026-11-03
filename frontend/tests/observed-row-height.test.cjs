const test = require('node:test');
const assert = require('node:assert/strict');
const { runtime, load } = require('./datasetRuntime.cjs');

// A duplicate group's Gallery commits its first layout at a 1 px placeholder width
// (one column of full-width tiles) and fixes it in its own layout effect, which
// React applies only after the row's layout effect has run. A mount-time read
// therefore sees a row 3-13x too tall; caching that height inflated the virtual
// offsets, the anchor correction moved the window, rows remounted and re-read the
// placeholder height again: a scroll fight that ended in a blank, pegged page.
test('row height comes from ResizeObserver deliveries, never the mount-time placeholder layout', (t) => {
  const oldObserver = global.ResizeObserver;
  const observers = [];
  global.ResizeObserver = class {
    constructor(fn) { this.fn = fn; this.targets = []; observers.push(this); }
    observe(target) { this.targets.push(target); }
    disconnect() { this.disconnected = true; }
  };
  t.after(() => { global.ResizeObserver = oldObserver; });
  const rt = runtime(); rt.api.useLayoutEffect = rt.api.useEffect;
  const { useObservedHeight } = load('hooks/useObservedHeight.ts', rt, {});

  let height = 3372; // placeholder single-column layout during the mount commit
  const element = { getBoundingClientRect: () => ({ height }) };
  const ref = { current: element };
  const reported = [];
  const onHeight = value => reported.push(value);
  let key = 1000;
  const render = () => rt.render(() => useObservedHeight(ref, onHeight, key));

  render();
  assert.deepEqual(reported, [], 'must not read the placeholder layout in the mount effect');
  assert.equal(observers.length, 1);
  assert.equal(observers[0].targets[0], element);

  height = 604.6; // the Gallery's corrected layout, as the observer's first delivery sees it
  observers[0].fn([]);
  assert.deepEqual(reported, [605]);

  height = 0; // detached / display:none
  observers[0].fn([]);
  assert.deepEqual(reported, [605], 'zero-height deliveries are ignored');

  key = 1400; height = 520; // layout width changed: re-subscribe, still observer-driven
  render();
  assert.equal(observers[0].disconnected, true);
  assert.deepEqual(reported, [605]);
  observers[1].fn([]);
  assert.deepEqual(reported, [605, 520]);

  rt.unmount();
  assert.equal(observers[1].disconnected, true);
});
