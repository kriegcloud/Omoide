/* eslint-disable @typescript-eslint/no-require-imports -- Node-only regression test loads the existing TypeScript source. */
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");
const sourceRoot = path.resolve(__dirname, "../..");

function hooks() {
  const slots = [];
  const pending = [];
  let cursor = 0;
  const changed = (old, next) => !old || !next || old.length !== next.length ||
    old.some((value, index) => value !== next[index]);
  const react = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = initial;
      return [slots[index], (value) => {
        slots[index] = typeof value === "function" ? value(slots[index]) : value;
      }];
    },
    useRef(initial) {
      const index = cursor++;
      slots[index] ??= { current: initial };
      return slots[index];
    },
    useEffect(run, dependencies) {
      const index = cursor++;
      if (changed(slots[index]?.dependencies, dependencies)) pending.push(() => {
        slots[index]?.cleanup?.();
        slots[index] = { dependencies, cleanup: run() };
      });
    },
    useCallback: (callback) => callback,
  };
  return {
    react,
    render(run) {
      cursor = 0;
      const result = run();
      while (pending.length) pending.shift()();
      return result;
    },
  };
}

class ElementStub {
  constructor() {
    this.listeners = new Map();
    this.style = {};
    this.dataset = {};
  }
  closest() { return null; }
  setAttribute() {}
  removeAttribute() {}
  addEventListener(name, handler) { this.listeners.set(name, handler); }
  removeEventListener(name) { this.listeners.delete(name); }
  getClientRects() { return [{}]; }
  setPointerCapture() { this.captured = true; }
  hasPointerCapture() { return this.captured; }
  releasePointerCapture() { this.captured = false; }
}

function load(name, mocks, globals, suffix = "") {
  const code = ts.transpileModule(fs.readFileSync(path.join(sourceRoot, name), "utf8") + suffix, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const exports = {};
  vm.runInNewContext(code, {
    exports, require: (key) => mocks[key] ?? {}, Set, Map,
    Element: ElementStub, HTMLElement: ElementStub,
    MutationObserver: class { observe() {} disconnect() {} },
    ...globals,
  });
  return exports;
}

test("global Escape cancels an active media marquee before clearing its selection", () => {
  const state = hooks();
  const registrations = new Set();
  const register = (read) => {
    registrations.add(read);
    return () => registrations.delete(read);
  };
  const window = new ElementStub();
  window.scrollX = window.scrollY = 0;
  window.innerHeight = 1000;
  window.requestAnimationFrame = () => 1;
  window.cancelAnimationFrame = () => {};
  window.setTimeout = () => {};
  window.scrollBy = () => {};
  const container = new ElementStub();
  const tile = new ElementStub();
  tile.dataset.selectableId = "1";
  tile.getBoundingClientRect = () => ({ left: 120, right: 140, top: 120, bottom: 140 });
  container.querySelectorAll = () => [tile];
  const selection = {
    selectedIds: new Set([1]),
    isSelecting: true,
    setSelected(ids) { selection.selectedIds = new Set(ids); },
    clear() { selection.selectedIds = new Set(); selection.isSelecting = false; },
    toggleSelecting() {},
  };
  const selectionModule = { useSelection: () => selection, useSelectionList() {} };
  const useGridSelection = load("hooks/useMarqueeSelection.ts", {
    react: state.react,
    "react-router-dom": { useLocation: () => ({ pathname: "/blur" }) },
    "../context/SelectionContext": selectionModule,
    "../hotkeys/useHotkey": { useHotkeyRegistry: () => register },
  }, { window }).useGridSelection;
  const containerRef = { current: container };
  const render = () => state.render(() => useGridSelection({
    containerRef, selecting: selection.isSelecting,
    selectedIds: selection.selectedIds, onSelectionChange: selection.setSelected,
  }));
  render();
  render();

  // Load the real global selection handler; exporting the private component in
  // this VM leaves the production module and its public API unchanged.
  const { GlobalHotkeys } = load("hotkeys/HotkeyProvider.tsx", {
    "./useHotkey": {
      useHotkey(binding, handler, options) { register(() => ({ bindings: [binding], handler, options })); },
      useHotkeys() {},
    },
    "./keymap": { gridBindings: [] },
    "../context/SelectionContext": selectionModule,
    "../context/UndoContext": { useUndo: () => ({ current: null }) },
  }, { window }, "\nexport { GlobalHotkeys };\n");
  GlobalHotkeys();
  const pointer = (position) => ({
    target: container, pointerId: 1, pointerType: "mouse", button: 0,
    pageX: position, pageY: position, clientX: position, clientY: position,
    preventDefault() {},
  });
  container.listeners.get("pointerdown")(pointer(100));
  window.listeners.get("pointermove")(pointer(150));
  assert.deepEqual([...selection.selectedIds], [1]);
  assert.equal(container.captured, true);

  const escape = { key: "Escape", target: container, defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true; } };
  const active = [...registrations].map((read) => read())
    .filter((entry) => entry.bindings.some((binding) => binding.key === "Escape"))
    .sort((a, b) => Number(b.options.scope === "page") - Number(a.options.scope === "page"));
  for (const entry of active) {
    if (entry.options.enabled === false || (entry.options.when && !entry.options.when(escape))) continue;
    if (entry.handler(escape) !== false) break;
  }
  assert.equal(selection.selectedIds.size, 0);
  assert.equal(selection.isSelecting, true, "first Escape clears selection while keeping selection mode");
  render();
  window.listeners.get("pointermove")?.(pointer(160));
  assert.equal(selection.selectedIds.size, 0, "continued pointer motion must not repopulate selection after Escape");
  assert.equal(container.captured, false, "Escape must release the active marquee pointer");
});
