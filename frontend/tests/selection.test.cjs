// Run with: node --test --test-isolation=none frontend/tests/selection.test.cjs
// No browser or new dependencies: execute the real TS/TSX with minimal hook/DOM
// adapters. These tests cover event contracts and geometry, not browser layout.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const React = require('react');

function hooks() {
  const slots = [];
  let cursor = 0;
  let dirty = false;
  let effects = [];
  const equal = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
  const api = {
    ...React,
    memo: (component) => component,
    useRef(value) { const i = cursor++; return slots[i] ??= { current: value }; },
    useState(value) {
      const i = cursor++;
      if (!(i in slots)) slots[i] = typeof value === 'function' ? value() : value;
      return [slots[i], (next) => {
        const value = typeof next === 'function' ? next(slots[i]) : next;
        if (!Object.is(value, slots[i])) { slots[i] = value; dirty = true; }
      }];
    },
    useCallback(fn, deps) {
      const i = cursor++;
      if (!slots[i] || !equal(slots[i].deps, deps)) slots[i] = { deps, fn };
      return slots[i].fn;
    },
    useEffect(fn, deps) {
      const i = cursor++;
      if (!slots[i] || !equal(slots[i].deps, deps)) effects.push(() => {
        slots[i]?.cleanup?.();
        slots[i] = { deps, cleanup: fn() };
      });
    },
  };
  return {
    api,
    render(fn, runEffects = true) {
      let value;
      for (let attempt = 0; attempt < 10; attempt++) {
        cursor = 0; dirty = false; effects = [];
        value = fn();
        if (runEffects) effects.forEach((effect) => effect());
        if (!dirty) return value;
      }
      throw new Error('Render did not settle');
    },
    unmount() { slots.forEach((slot) => slot?.cleanup?.()); },
  };
}

function load(relative, runtime, overrides = {}) {
  const filename = path.resolve(__dirname, '../src', relative);
  const theme = { shape: { borderRadius: 4 }, palette: { primary: { main: 'blue' } } };
  const ui = new Proxy({ useTheme: () => theme }, { get: (target, key) => target[key] ?? key });
  const modules = {
    react: runtime.api,
    '@mui/material': ui,
    '@mui/material/styles': { alpha: (color) => color },
    'react-router-dom': { Link: 'RouterLink', useLocation: () => ({ pathname: '/images' }), useNavigate: () => () => {} },
    '../context/SelectionContext': { useSelection: () => ({ setSelected() {}, isSelecting: false }), useSelectionList() {} },
    '../config': { API: '/api' },
    '../urlUtils': { encodeFilePath: encodeURIComponent },
    './SelectableTileFrame': { default: 'SelectableTileFrame', __esModule: true },
    '../hooks/useMarqueeSelection': { ...loadHookHelpers(), __esModule: true },
    ...overrides,
  };
  function loadHookHelpers() {
    // Frame's helper import is resolved to the actual hook module, if needed.
    if (relative === 'hooks/useMarqueeSelection.ts') return {};
    return load('hooks/useMarqueeSelection.ts', runtime);
  }
  const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
    fileName: filename,
  }).outputText;
  const module = { exports: {} };
  const localRequire = (id) => {
    if (id in modules) return modules[id];
    if (id.startsWith('@mui/icons-material/')) return { __esModule: true, default: id };
    return require(id);
  };
  new Function('require', 'module', 'exports', output)(localRequire, module, module.exports);
  return module.exports;
}

function find(node, predicate) {
  if (!node || typeof node !== 'object') return undefined;
  if (predicate(node)) return node;
  for (const child of [node.props?.children].flat(Infinity)) {
    const found = find(child, predicate);
    if (found) return found;
  }
}
function gesture(extra = {}) {
  return { ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, detail: 1,
    preventDefault() { this.defaultPrevented = true; }, stopPropagation() {}, ...extra };
}
class Surface {
  listeners = new Map();
  addEventListener(name, fn) { if (!this.listeners.has(name)) this.listeners.set(name, new Set()); this.listeners.get(name).add(fn); }
  removeEventListener(name, fn) { this.listeners.get(name)?.delete(fn); }
  emit(name, event = {}) { this.listeners.get(name)?.forEach((fn) => fn(event)); }
}
class ElementStub extends Surface {
  dataset = {}; style = { userSelect: '' }; children = []; parentElement = null;
  scrollTop = 0; scrollLeft = 0; scrollHeight = 400; clientHeight = 400; clientWidth = 400;
  clientTop = 0; clientLeft = 0; overflowY = 'visible'; measurements = 0;
  rect = { left: 0, right: 400, top: 0, bottom: 400, width: 400, height: 400 };
  getBoundingClientRect() { this.measurements++; return this.rect; }
  getClientRects() { return [this.rect]; }
  querySelectorAll() { return this.children; }
  closest(selector) { return selector.includes('[data-selectable-id]') && this.dataset.selectableId ? this : null; }
  contains(target) { return target === this || this.children.some((child) => child.contains(target)); }
  matches() { return false; }
  setAttribute() {} removeAttribute() {}
  setPointerCapture() {} hasPointerCapture() { return false; } releasePointerCapture() {}
  scrollCalls = [];
  scrollBy(options) { this.scrollCalls.push(options); this.scrollTop += options.top; }
}
function dom(t) {
  const saved = {};
  const cleanups = [];
  const win = new Surface();
  const frames = new Map();
  const observers = [];
  let nextFrame = 0;
  Object.assign(win, {
    scrollX: 0, scrollY: 0, innerHeight: 800, innerWidth: 1200, scrollCalls: [],
    requestAnimationFrame(fn) { frames.set(++nextFrame, fn); return nextFrame; },
    cancelAnimationFrame(id) { frames.delete(id); },
    setTimeout() {},
    getComputedStyle(element) { return { overflowY: element.overflowY }; },
    scrollBy(...args) { this.scrollCalls.push(args); this.scrollY += typeof args[0] === 'object' ? args[0].top : args[1]; },
  });
  class Observer {
    constructor(callback) { this.callback = callback; observers.push(this); }
    observe() {} disconnect() { this.disconnected = true; }
  }
  const globals = { window: win, document: { body: new ElementStub(), documentElement: new ElementStub() },
    Element: ElementStub, HTMLElement: ElementStub, MutationObserver: Observer, ResizeObserver: Observer,
    getComputedStyle: win.getComputedStyle };
  for (const [name, value] of Object.entries(globals)) { saved[name] = global[name]; global[name] = value; }
  t.after(() => { cleanups.forEach((fn) => fn()); for (const name of Object.keys(globals)) { if (saved[name] === undefined) delete global[name]; else global[name] = saved[name]; } });
  return { win, frames, observers, cleanups, tick() { const pending = [...frames.values()]; frames.clear(); pending.forEach((fn) => fn(16)); } };
}
function grid(t, { nested = false, selected = [], selecting = true } = {}) {
  const env = dom(t);
  const container = new ElementStub();
  const scroller = new ElementStub();
  if (nested) {
    scroller.overflowY = 'auto'; scroller.scrollHeight = 2000;
    scroller.rect = { left: 0, right: 400, top: 100, bottom: 500, width: 400, height: 400 };
    container.parentElement = scroller; scroller.children = [container];
  }
  container.children = Array.from({ length: 6 }, (_, i) => {
    const tile = new ElementStub(); tile.parentElement = container; tile.dataset.selectableId = String(i + 1);
    tile.rect = { left: (i % 3) * 100, right: (i % 3) * 100 + 90, top: Math.floor(i / 3) * 100 + 100,
      bottom: Math.floor(i / 3) * 100 + 190, width: 90, height: 90 };
    return tile;
  });
  const runtime = hooks();
  const { useGridSelection } = load('hooks/useMarqueeSelection.ts', runtime);
  const changes = [];
  const options = { containerRef: { current: container }, selecting, selectedIds: new Set(selected),
    onSelectionChange(ids) { changes.push(new Set(ids)); options.selectedIds = ids; } };
  const render = () => runtime.render(() => useGridSelection(options));
  const hook = render();
  env.cleanups.push(() => runtime.unmount());
  function drag(x = 250, y = nested ? 490 : 790) {
    container.emit('pointerdown', gesture({ button: 0, pointerType: 'mouse', pointerId: 1, target: container,
      pageX: 10, pageY: 110, clientX: 10, clientY: 110 }));
    env.win.emit('pointermove', gesture({ pointerId: 1, clientX: x, clientY: y, pageX: x, pageY: y }));
  }
  return { ...env, runtime, container, scroller, changes, options, render, hook, drag };
}

test('checkbox Shift-click preserves modifiers and selects the visual range', (t) => {
  const g = grid(t);
  g.hook.onItemClick(1, gesture());
  const runtime = hooks();
  const Frame = load('components/SelectableTileFrame.tsx', runtime).default;
  let forwarded;
  const frame = runtime.render(() => Frame({ id: 6, selecting: true, selected: false,
    onSelectionClick(id, event) { forwarded = event; return g.hook.onItemClick(id, event); } }));
  find(frame, (node) => node.type === 'Checkbox').props.onClick(gesture({ shiftKey: true, metaKey: true, altKey: true }));
  assert.equal(forwarded.ctrlKey, true);
  assert.equal(forwarded.shiftKey, true);
  assert.equal(forwarded.metaKey, true);
  assert.equal(forwarded.altKey, true);
  assert.deepEqual([...g.changes.at(-1)], [1, 2, 3, 4, 5, 6]);
});

test('FaceCard media-to-media open retains root background and replaces current entry', () => {
  const runtime = hooks(); const navigation = [];
  const root = { pathname: '/images', key: 'root' };
  const current = { pathname: '/medium/1', state: { backgroundLocation: root } };
  const FaceCard = load('components/FaceCard.tsx', runtime, {
    'react-router-dom': { useLocation: () => current, useNavigate: () => (...args) => navigation.push(args) },
  }).default;
  const frame = runtime.render(() => FaceCard({ face: { id: 2, media_id: 3, thumbnail_path: 'fixture.jpg', timestamp: 12 }, isProfile: false }));
  frame.props.onOpen();
  assert.equal(navigation[0][1].state.backgroundLocation, root);
  assert.equal(navigation[0][1].replace, true);
  assert.equal(navigation[0][1].state.sceneStart, 12);
  assert.equal(navigation[0][1].state.autoplayVideo, true);
});

test('marquee scroll uses instant behavior to override document smooth scrolling', (t) => {
  const g = grid(t); g.drag(); g.tick();
  assert.equal(g.win.scrollCalls.at(-1)[0].behavior, 'instant');
});
test('marquee scrolls nearest overflow ancestor instead of window', (t) => {
  const g = grid(t, { nested: true }); g.drag(); g.tick();
  assert.ok(g.scroller.scrollCalls.length > 0);
  assert.equal(g.scroller.scrollCalls[0].behavior, 'instant');
  assert.equal(g.win.scrollCalls.length, 0);
});
test('marquee caches tile measurements across pointer moves and animation frames', (t) => {
  const g = grid(t); g.drag(250, 300);
  const measurements = g.container.children.map((tile) => tile.measurements);
  g.tick(); g.tick();
  g.win.emit('pointermove', gesture({ pointerId: 1, clientX: 251, clientY: 301, pageX: 251, pageY: 301 }));
  assert.deepEqual(g.container.children.map((tile) => tile.measurements), measurements);
});
test('marquee skips publishing identical membership', (t) => {
  const g = grid(t); g.drag(250, 300);
  const published = g.changes.length; g.tick(); g.tick();
  assert.equal(g.changes.length, published);
});
test('window blur cancels marquee animation and pointer listeners', (t) => {
  const g = grid(t); g.drag(); g.win.emit('blur');
  assert.equal(g.frames.size, 0);
  assert.equal(g.win.listeners.get('pointermove')?.size ?? 0, 0);
  assert.equal(g.container.style.userSelect, '');
});
test('pointercancel cancels marquee animation', (t) => {
  const g = grid(t); g.drag(); g.win.emit('pointercancel', { pointerId: 1 });
  assert.equal(g.frames.size, 0);
});

for (const page of ['Blurry', 'LowResolution', 'NoExifDate', 'ShortVideos', 'Untagged', 'Nopersons']) {
  test(`${page} review grid honors global selection with empty local selection`, () => {
    const runtime = hooks();
    let gridOptions;
    const localSelected = new Set();
    const modules = {
      '../context/SelectionContext': { useSelection: () => ({ isSelecting: true }) },
      '../hooks/useMarqueeSelection': { useGridSelection(options) { gridOptions = options; return {}; } },
      '../hooks/useCursorList': { useCursorList: () => ({ items: [], total: 0, selectedIds: localSelected, setSelectedIds() {} }) },
      '../TaskEventsContext': { useTaskCompletionVersion: () => 0, useTaskEvents: () => ({ activeTasks: [] }) },
      '../components/RerunProcessorsDialog': { RerunProcessorsDialog: 'RerunProcessorsDialog' },
      '../components/BulkResolveToolbar': { __esModule: true, default: 'Toolbar' },
      '../components/ReviewMediaGrid': { __esModule: true, default: 'Grid' },
      '../components/SelectableMediaTile': { __esModule: true, default: 'Tile' },
      '../formatUtils': { formatBytes: String, formatDuration: String },
    };
    for (const service of ['blur', 'lowresolution', 'noexifdate', 'shortVideos', 'shortvideos', 'untagged', 'nopersons', 'noPersons']) {
      modules[`../services/${service}`] = {};
    }
    const Page = load(`pages/${page}Page.tsx`, runtime, modules).default;
    runtime.render(() => Page(), false);
    assert.equal(gridOptions.selecting, true);
    assert.equal(gridOptions.selectedIds, localSelected);
  });
}

function tileHarness(g, props = {}) {
  const runtime = hooks();
  const navigations = [];
  const Frame = load('components/SelectableTileFrame.tsx', runtime, {
    'react-router-dom': { Link: 'RouterLink', useNavigate: () => (...args) => navigations.push(args) },
  }).default;
  const frameProps = { id: 6, selected: false, selecting: true, href: '/medium/6',
    linkState: { backgroundLocation: { pathname: '/blur' } }, replace: true,
    onSelectionClick: (id, event) => g.hook.onItemClick(id, event), ...props };
  const element = new ElementStub();
  const render = () => runtime.render(() => Frame(frameProps));
  const click = (detail, modifiers = {}) => {
    const event = gesture({ detail, target: element, currentTarget: element, ...modifiers });
    const frame = render();
    frame.props.onClickCapture(event);
    if (!event.defaultPrevented) frame.props.onClick(event);
    return event;
  };
  const doubleClick = () => render().props.onDoubleClick?.(gesture({ detail: 2, target: element, currentTarget: element }));
  return { render, frameProps, element, click, doubleClick, navigations };
}

test('double-click opens once and restores a Shift range and its previous anchor', (t) => {
  const g = grid(t, { selected: [99] });
  g.hook.onItemClick(1, gesture());
  const tile = tileHarness(g);
  tile.click(1, { shiftKey: true });
  assert.deepEqual([...g.changes.at(-1)], [99, 1, 2, 3, 4, 5, 6]);
  tile.click(2, { shiftKey: true }); tile.doubleClick();
  assert.deepEqual([...g.changes.at(-1)], [99, 1]);
  assert.deepEqual(tile.navigations, [['/medium/6', { state: tile.frameProps.linkState, replace: true }]]);
  g.hook.onItemClick(3, gesture({ shiftKey: true }));
  assert.deepEqual([...g.changes.at(-1)], [99, 1, 2, 3]);
});
test('double-click restores the last selected tile after selection mode becomes false', (t) => {
  const g = grid(t, { selected: [6] });
  const tile = tileHarness(g, { selected: true });
  tile.click(1);
  assert.equal(g.changes.at(-1).size, 0);
  g.options.selecting = false; g.hook = g.render();
  tile.frameProps.selecting = false; tile.frameProps.selected = false;
  assert.equal(tile.click(2).defaultPrevented, true);
  tile.doubleClick();
  assert.deepEqual([...g.changes.at(-1)], [6]);
  assert.equal(tile.navigations.length, 1);
});
test('ordinary single clicks still toggle immediately without opening', (t) => {
  const g = grid(t); const tile = tileHarness(g);
  assert.equal(tile.click(1).defaultPrevented, true);
  assert.deepEqual([...g.changes.at(-1)], [6]);
  assert.equal(tile.navigations.length, 0);
  tile.click(1);
  assert.equal(g.changes.at(-1).size, 0);
});
test('Open button is focusable, bypasses selection and only appears while selecting', (t) => {
  const g = grid(t); let opened = 0;
  const tile = tileHarness(g, { onOpen: () => opened++ });
  const button = find(tile.render(), (node) => node.props?.['aria-label'] === 'Open');
  assert.ok(button);
  assert.notEqual(button.props.tabIndex, -1);
  assert.equal(button.props['data-tile-control'], true);
  assert.equal(button.props['data-no-marquee'], true);
  button.props.onClick(gesture());
  assert.equal(opened, 1); assert.equal(g.changes.length, 0);
  tile.frameProps.selecting = false;
  assert.equal(find(tile.render(), (node) => node.props?.['aria-label'] === 'Open'), undefined);
});
test('Enter opens focused tiles in both modes; Space selects without opening', (t) => {
  const g = grid(t); const tile = tileHarness(g);
  const key = (value, extra = {}) => tile.render().props.onKeyDown(gesture({ key: value,
    target: tile.element, currentTarget: tile.element, ...extra }));
  key('Enter'); assert.equal(tile.navigations.length, 1); assert.equal(g.changes.length, 0);
  key(' '); assert.deepEqual([...g.changes.at(-1)], [6]); assert.equal(tile.navigations.length, 1);
  tile.frameProps.selecting = false;
  key('Enter'); assert.equal(tile.navigations.length, 2);
});
test('nested controls and portal actions retain their own keyboard and double-click events', (t) => {
  const g = grid(t); const tile = tileHarness(g);
  const control = new ElementStub(); control.closest = () => control;
  tile.element.children = [control];
  tile.render().props.onKeyDown(gesture({ key: 'Enter', target: control, currentTarget: tile.element }));
  tile.click(1);
  tile.render().props.onDoubleClick(gesture({ target: control, currentTarget: tile.element }));
  tile.render().props.onDoubleClick(gesture({ target: new ElementStub(), currentTarget: tile.element }));
  assert.equal(tile.navigations.length, 0);
  assert.deepEqual([...g.changes.at(-1)], [6]);
});
test('checkbox double-click remains selection-only', (t) => {
  const g = grid(t); const tile = tileHarness(g);
  const checkbox = find(tile.render(), (node) => node.type === 'Checkbox');
  checkbox.props.onClick(gesture({ detail: 1 }));
  checkbox.props.onClick(gesture({ detail: 2 }));
  tile.doubleClick();
  assert.equal(g.changes.at(-1).size, 0); assert.equal(tile.navigations.length, 0);
});
test('marquee easing grows with edge depth and caps at 48 pixels', () => {
  const runtime = hooks();
  const { marqueeScrollVelocity: speed } = load('hooks/useMarqueeSelection.ts', runtime);
  assert.equal(speed(400, 0, 800), 0);
  assert.equal(speed(720, 0, 800), 0);
  assert.equal(speed(760, 0, 800), 12);
  assert.equal(speed(800, 0, 800), 48);
  assert.equal(speed(900, 0, 800), 48);
  assert.equal(speed(40, 0, 800), -12);
  assert.equal(speed(-100, 0, 800), -48);
  assert.equal(speed(50, 100, 80), 0);
});
test('ancestor scroll advances the selection in cached content coordinates', (t) => {
  const g = grid(t, { nested: true });
  g.container.children[5].rect = { left: 100, right: 190, top: 510, bottom: 600, width: 90, height: 90 };
  g.drag(250, 490);
  assert.equal(g.changes.at(-1).has(6), false);
  g.tick();
  assert.equal(g.changes.at(-1).has(6), true);
  assert.equal(g.container.children[5].measurements, 1);
});
test('grid itself can be the scrollport and starting scroll does not trigger a drag', (t) => {
  const g = grid(t);
  g.container.overflowY = 'auto'; g.container.scrollHeight = 2000; g.container.scrollTop = 300;
  g.container.emit('pointerdown', gesture({ button: 0, pointerType: 'mouse', pointerId: 1, target: g.container,
    pageX: 10, pageY: 110, clientX: 10, clientY: 110 }));
  g.win.emit('pointermove', gesture({ pointerId: 1, clientX: 11, clientY: 111, pageX: 11, pageY: 111 }));
  assert.equal(g.frames.size, 0);
  g.win.emit('pointermove', gesture({ pointerId: 1, clientX: 250, clientY: 390, pageX: 250, pageY: 390 }));
  g.tick();
  assert.ok(g.container.scrollCalls.length > 0); assert.equal(g.win.scrollCalls.length, 0);
});
test('window resize invalidates cached geometry once', (t) => {
  const g = grid(t); g.drag(250, 300);
  g.win.emit('resize'); g.tick();
  assert.equal(g.container.children[0].measurements, 2);
  g.tick(); assert.equal(g.container.children[0].measurements, 2);
});

test('scrollport marquee stays in page coordinates for an outside overlay', (t) => {
  const g = grid(t);
  g.container.overflowY = 'auto'; g.container.scrollHeight = 2000; g.container.scrollTop = 300;
  g.drag(250, 390); g.tick();
  const result = g.render();
  assert.equal(result.marqueeRect.top, 110 - (g.container.scrollTop - 300));
});
test('overlay inside a scrolled grid accounts for its own scroll offset', (t) => {
  const env = dom(t); env.win.scrollY = 50;
  const container = new ElementStub(); container.scrollTop = 300;
  const runtime = hooks();
  const Box = load('components/MarqueeSelectionBox.tsx', runtime).default;
  const node = Box({ container, rect: { top: 150, left: 0, width: 20, height: 30 } });
  assert.equal(node.props.sx.top, 400);
});

test('drag inside an internal face scroller uses that scroller and excludes pinned tiles', (t) => {
  const g = grid(t);
  const internal = new ElementStub(); internal.parentElement = g.container;
  internal.overflowY = 'auto'; internal.scrollHeight = 2000;
  internal.rect = { left: 0, right: 400, top: 100, bottom: 500, width: 400, height: 400 };
  internal.children = g.container.children.slice(3);
  for (const tile of internal.children) tile.parentElement = internal;
  g.container.emit('pointerdown', gesture({ button: 0, pointerType: 'mouse', pointerId: 1, target: internal,
    pageX: 10, pageY: 110, clientX: 10, clientY: 110 }));
  g.win.emit('pointermove', gesture({ pointerId: 1, clientX: 250, clientY: 490, pageX: 250, pageY: 490 }));
  g.tick();
  assert.equal(internal.scrollCalls.length, 1);
  assert.equal(g.win.scrollCalls.length, 0);
  assert.deepEqual([...g.changes.at(-1)], [4, 5, 6]);
});

test('tile mutation invalidates cached geometry; overlay chrome does not', (t) => {
  const g = grid(t); g.drag(250, 300);
  const mutation = g.observers.at(-1);
  const chrome = new ElementStub(); chrome.querySelector = () => null;
  mutation.callback([{ type: 'attributes', target: chrome }]); g.tick();
  assert.equal(g.container.children[0].measurements, 1);
  const tile = g.container.children[0]; tile.matches = () => true;
  tile.rect = { ...tile.rect, left: 600, right: 690 };
  mutation.callback([{ type: 'attributes', target: tile }]); g.tick();
  assert.equal(tile.measurements, 2); assert.equal(g.changes.at(-1).has(1), false);
});
test('virtualized scrolling retains previously intersected tiles after they unmount', (t) => {
  const g = grid(t, { nested: true }); g.drag(250, 490); g.tick();
  const removed = g.container.children.splice(0, 3);
  removed.forEach((tile) => { tile.matches = () => true; });
  g.observers.at(-1).callback([{ type: 'childList', addedNodes: [], removedNodes: removed }]);
  g.tick();
  assert.deepEqual([...g.changes.at(-1)].sort(), [1, 2, 3, 4, 5, 6]);
});
