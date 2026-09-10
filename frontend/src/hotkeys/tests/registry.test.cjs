/* eslint-disable @typescript-eslint/no-require-imports -- Standalone Node harness exercises the real TypeScript hotkey runtime without a browser runner. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');

const sourceRoot = path.resolve(__dirname, '../..');

class ElementStub {
  constructor({ tag = 'div', role, editable = false, parent = null, classes = [], hidden = false, ariaHidden = false } = {}) {
    Object.assign(this, { tag, role, isContentEditable: editable, parentElement: parent, classes, hidden, ariaHidden });
  }
  contains(other) {
    for (let current = other; current; current = current.parentElement) if (current === this) return true;
    return false;
  }
  closest(selector) {
    if (selector.includes('textarea') && (['input', 'textarea', 'select'].includes(this.tag) ||
      this.isContentEditable || ['textbox', 'slider', 'combobox'].includes(this.role))) return this;
    return this.parentElement?.closest(selector) ?? null;
  }
}

class KeyEvent {
  constructor(type, props = {}) {
    Object.assign(this, { type, key: '', ctrlKey: false, metaKey: false, altKey: false, shiftKey: false,
      repeat: false, isComposing: false, keyCode: 0, defaultPrevented: false, propagationStopped: false,
      immediateStopped: false, target: null }, props);
  }
  preventDefault() { this.defaultPrevented = true; }
  stopPropagation() { this.propagationStopped = true; }
  stopImmediatePropagation() { this.immediateStopped = true; this.propagationStopped = true; }
  composedPath() { return [this.shadowTarget ?? this.target]; }
}

function hookHarness() {
  const slots = [];
  const pending = [];
  let cursor = 0;
  let context;
  const depsChanged = (before, after) => !before || !after || before.length !== after.length || before.some((value, i) => value !== after[i]);
  const effect = (fn, deps) => {
    const index = cursor++;
    if (depsChanged(slots[index]?.deps, deps)) pending.push(() => {
      slots[index]?.cleanup?.();
      slots[index] = { deps, cleanup: fn() };
    });
  };
  const memo = (fn, deps) => {
    const index = cursor++;
    if (depsChanged(slots[index]?.deps, deps)) slots[index] = { deps, value: fn() };
    return slots[index].value;
  };
  const react = {
    useRef(initial) { const index = cursor++; slots[index] ??= { current: initial }; return slots[index]; },
    useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial;
      return [slots[index], next => { slots[index] = typeof next === 'function' ? next(slots[index]) : next; }]; },
    useMemo: memo, useCallback: (fn, deps) => memo(() => fn, deps), useEffect: effect, useLayoutEffect: effect,
    useContext: () => context, useId: () => 'test-dialog-title',
  };
  return { react, setContext: next => { context = next; }, render: fn => {
    cursor = 0;
    const result = fn();
    while (pending.length) pending.shift()();
    return result;
  } };
}

function load(relativePath, mocks, globals) {
  const filename = path.join(sourceRoot, relativePath);
  const source = fs.readFileSync(filename, 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, require: name => {
    if (!(name in mocks)) throw new Error(`Unmocked module ${name}`);
    return mocks[name];
  }, console, Set, Map, Symbol, KeyboardEvent: KeyEvent, Element: ElementStub, HTMLElement: ElementStub, ...globals }, { filename });
  return exports;
}

const jsx = (type, props) => ({ type, props });
const jsxRuntime = { jsx, jsxs: jsx, Fragment: 'Fragment' };

function registry() {
  const hooks = hookHarness();
  const listeners = [];
  const document = {
    modals: [], menuOpen: false, gridPresent: true, activeElement: new ElementStub(),
    querySelectorAll(selector) {
      if (selector.includes('MuiModal-root')) return this.modals.filter(modal => !modal.hidden && !modal.classes.includes('MuiMenu-root') && (!selector.includes('[aria-hidden=') || !modal.ariaHidden));
      return [];
    },
    querySelector(selector) {
      if (selector.includes('MuiMenu-root')) return this.menuOpen ? new ElementStub({ role: 'menu' }) : null;
      if (selector.includes('data-selection-grid')) return this.gridPresent ? new ElementStub() : null;
      return null;
    },
  };
  const window = {
    addEventListener(type, handler, capture = false) { listeners.push({ type, handler, capture }); },
    removeEventListener(type, handler) { const index = listeners.findIndex(listener => listener.type === type && listener.handler === handler); if (index >= 0) listeners.splice(index, 1); },
  };
  const globals = { window, document };
  const keymap = load('hotkeys/keymap.ts', {}, globals);
  const context = { Provider: 'HotkeyContext.Provider' };
  const provider = load('hotkeys/HotkeyProvider.tsx', {
    react: hooks.react, 'react/jsx-runtime': jsxRuntime, './HotkeyContext': { HotkeyContext: context },
    './keymap': keymap, './useHotkey': { useHotkey() {}, useHotkeys() {} },
    './GridKeyboardNavigation': { GridKeyboardNavigation: 'GridKeyboardNavigation' },
    '../context/SelectionContext': {}, '../context/UndoContext': {},
    './HotkeyHelpDialog': { default: 'HotkeyHelpDialog', __esModule: true },
  }, globals).HotkeyProvider;
  const render = () => hooks.render(() => provider({ children: null }));
  const tree = render();
  const api = tree.props.value;
  function add(binding, handler = () => {}, options = {}) {
    return api.register(() => ({ bindings: Array.isArray(binding) ? binding : [binding], handler, options }));
  }
  function dispatch(key, props = {}, targetHandler = () => {}) {
    const event = new KeyEvent('keydown', { key, target: document.activeElement, ...props });
    for (const listener of listeners.filter(listener => listener.type === 'keydown' && listener.capture)) {
      listener.handler(event);
      if (event.immediateStopped) break;
    }
    if (!event.propagationStopped) targetHandler(event);
    for (const listener of listeners.filter(listener => listener.type === 'keydown' && !listener.capture)) {
      if (event.propagationStopped) break;
      listener.handler(event);
    }
    return event;
  }
  return { keymap, add, dispatch, document, window, listeners, api, render, globals, context };
}

test('binding matching is case-insensitive and modifiers are exact', () => {
  const { keymap } = registry();
  assert.ok(keymap.matchesBinding({ key: 'r' }, new KeyEvent('keydown', { key: 'R' })));
  assert.ok(!keymap.matchesBinding({ key: 'r' }, new KeyEvent('keydown', { key: 'R', shiftKey: true })));
  assert.ok(keymap.matchesBinding({ key: 'r', shift: true }, new KeyEvent('keydown', { key: 'R', shiftKey: true })));
  for (const modifier of ['ctrlKey', 'metaKey', 'altKey']) assert.ok(!keymap.matchesBinding({ key: 's' }, new KeyEvent('keydown', { key: 's', [modifier]: true })));
});

test('question mark and plus work on keyboard layouts that require Shift', () => {
  const { keymap } = registry();
  for (const key of ['?', '+']) for (const shiftKey of [false, true]) {
    assert.ok(keymap.matchesBinding({ key }, new KeyEvent('keydown', { key, shiftKey })));
  }
});

test('one capture listener survives rerenders and unregister removes bindings', () => {
  const r = registry();
  let calls = 0;
  const unregister = r.add({ key: 's' }, () => { calls++; });
  r.render(); r.render();
  assert.equal(r.listeners.filter(listener => listener.type === 'keydown').length, 1);
  r.dispatch('s'); unregister(); r.dispatch('s');
  assert.equal(calls, 1);
});

test('page handler wins over global binding for the same key', () => {
  const r = registry();
  const calls = [];
  r.add({ key: 's' }, () => calls.push('page'), { scope: 'page' });
  r.add({ key: 's' }, () => calls.push('global'), { scope: 'global' });
  r.dispatch('s');
  assert.deepEqual(calls, ['page']);
});

test('a page-owned local roving handler takes priority over the global media fallback', () => {
  const r = registry(); const calls = [];
  r.add({ key: 'ArrowRight' }, () => calls.push('global media fallback'), { scope: 'global' });
  r.add({ key: 'ArrowRight' }, () => {}, { scope: 'page', local: true });
  r.dispatch('ArrowRight', {}, () => calls.push('page roving handler'));
  assert.deepEqual(calls, ['page roving handler']);
});

test('disabled or rejected when conditions allow the next active binding', () => {
  const r = registry();
  const calls = [];
  r.add({ key: 's' }, () => calls.push('global'));
  r.add({ key: 's' }, () => calls.push('disabled'), { scope: 'page', enabled: false });
  r.add({ key: 's' }, () => calls.push('condition'), { scope: 'page', when: () => false });
  r.dispatch('s');
  assert.deepEqual(calls, ['global']);
});

test('only the top dialog handles keys and unknown dialogs suspend page/global keys', () => {
  const r = registry();
  const lower = new ElementStub(); const upper = new ElementStub();
  const calls = [];
  r.document.modals = [lower, upper];
  r.add({ key: 'r' }, () => calls.push('global'));
  r.add({ key: 'r' }, () => calls.push('page'), { scope: 'page' });
  r.add({ key: 'r' }, () => calls.push('lower'), { scope: 'dialog', dialogRef: { current: lower } });
  r.add({ key: 'r' }, () => calls.push('upper'), { scope: 'dialog', dialogRef: { current: upper } });
  r.dispatch('r');
  r.document.modals.push(new ElementStub());
  r.dispatch('r');
  assert.deepEqual(calls, ['upper']);
});

test('hidden dialogs and menus do not replace the top visible dialog', () => {
  const r = registry(); const visible = new ElementStub();
  r.document.modals = [visible, new ElementStub({ hidden: true }), new ElementStub({ classes: ['MuiMenu-root'] })];
  assert.equal(r.keymap.getTopModal(), visible);
});

test('input textarea select contenteditable textbox slider and combobox pause normal bindings', () => {
  const r = registry(); let calls = 0;
  r.add({ key: 's' }, () => { calls++; });
  for (const attributes of [{ tag: 'input' }, { tag: 'textarea' }, { tag: 'select' }, { editable: true },
    { role: 'textbox' }, { role: 'slider' }, { role: 'combobox' }]) {
    const parent = new ElementStub(attributes);
    r.dispatch('s', { target: new ElementStub({ parent }) });
  }
  assert.equal(calls, 0);
});

test('shadow-root editable targets are guarded and explicit input opt-in works', () => {
  const r = registry(); let ordinary = 0; let optedIn = 0;
  r.add({ key: 's' }, () => { ordinary++; });
  r.add({ key: 'a' }, () => { optedIn++; }, { allowInInput: true });
  const target = new ElementStub(); const shadowTarget = new ElementStub({ tag: 'input' });
  r.dispatch('s', { target, shadowTarget }); r.dispatch('a', { target, shadowTarget });
  assert.equal(ordinary, 0); assert.equal(optedIn, 1);
});

test('menus pause normal bindings and honor explicit menu opt-in', () => {
  const r = registry(); let ordinary = 0; let optedIn = 0;
  r.document.menuOpen = true;
  r.add({ key: 's' }, () => { ordinary++; });
  r.add({ key: 'a' }, () => { optedIn++; }, { allowInMenu: true });
  r.dispatch('s'); r.dispatch('a');
  assert.equal(ordinary, 0); assert.equal(optedIn, 1);
});

test('default-prevented and IME events do not invoke registry handlers', () => {
  const r = registry(); let calls = 0;
  r.add({ key: 's' }, () => { calls++; });
  r.dispatch('s', { defaultPrevented: true });
  r.dispatch('s', { isComposing: true });
  r.dispatch('s', { keyCode: 229 });
  assert.equal(calls, 0);
});

test('destructive repeated keys are consumed while non-destructive repeats still work', () => {
  const r = registry(); let destructive = 0; let ordinary = 0;
  r.add({ key: 'Delete', destructive: true }, () => { destructive++; });
  r.add({ key: 'r' }, () => { ordinary++; });
  const deletion = r.dispatch('Delete', { repeat: true });
  r.dispatch('r', { repeat: true });
  assert.equal(destructive, 0); assert.equal(ordinary, 1);
  assert.ok(deletion.defaultPrevented && deletion.immediateStopped);
});

test('local grid bindings keep target dispatch when allowed', () => {
  const r = registry(); let tileEvents = 0;
  r.add(r.keymap.gridBindings, () => {}, { local: true });
  const event = r.dispatch('Enter', {}, () => { tileEvents++; });
  assert.equal(tileEvents, 1); assert.equal(event.defaultPrevented, false);
});

test('local grid registrations preserve editable controls own Enter and arrow handlers', () => {
  const r = registry(); let controlEvents = 0;
  r.add(r.keymap.gridBindings, () => {}, { local: true });
  r.dispatch('Enter', { target: new ElementStub({ tag: 'input' }) }, () => { controlEvents++; });
  r.dispatch('ArrowDown', { target: new ElementStub({ role: 'combobox' }) }, () => { controlEvents++; });
  r.dispatch('ArrowRight', { target: new ElementStub({ role: 'slider' }) }, () => { controlEvents++; });
  assert.equal(controlEvents, 3, 'ignoring hotkeys while editing must not cancel the controls own key handlers');
});

test('existing element handlers share the IME and default-prevented guard', () => {
  const r = registry(); let gridEvents = 0;
  r.add(r.keymap.gridBindings, () => {}, { local: true });
  const localHandler = event => { if (r.keymap.isHotkeyAllowed(event)) gridEvents++; };
  r.dispatch('ArrowRight', { isComposing: true }, localHandler);
  r.dispatch('ArrowRight', { keyCode: 229 }, localHandler);
  r.dispatch('ArrowRight', { defaultPrevented: true }, localHandler);
  assert.equal(gridEvents, 0, 'legacy roving handlers must use the shared IME/defaultPrevented guard');
});

test('a modal still receives its own control arrows and Enter while background grids are paused', () => {
  const r = registry(); const modal = new ElementStub(); let dialogEvents = 0;
  r.document.modals = [modal];
  r.add(r.keymap.gridBindings, () => {}, { local: true });
  r.dispatch('ArrowDown', { target: new ElementStub({ role: 'combobox', parent: modal }) }, () => { dialogEvents++; });
  r.dispatch('Enter', { target: new ElementStub({ parent: modal }) }, () => { dialogEvents++; });
  assert.equal(dialogEvents, 2, 'dialog controls retain keyboard behavior when a background page has a grid');
});

test('MUI owns Escape in open dialogs and menus', () => {
  const r = registry(); let closes = 0;
  r.add(r.keymap.gridBindings, () => {}, { local: true });
  r.document.modals = [new ElementStub()];
  r.dispatch('Escape', {}, event => { closes++; event.stopPropagation(); });
  r.document.modals = []; r.document.menuOpen = true;
  r.dispatch('Escape', {}, event => { closes++; event.stopPropagation(); });
  assert.equal(closes, 2);
});

test('help lists active winning bindings and snapshots the top dialog scope', () => {
  const r = registry();
  r.add({ key: 's', description: 'global select' });
  r.add({ key: 's', description: 'page save' }, () => {}, { scope: 'page' });
  r.add({ key: 'r', description: 'disabled' }, () => {}, { enabled: false });
  r.api.openHelp();
  let help = r.render().props.children.at(-1).props;
  assert.ok(help.open);
  assert.deepEqual(Array.from(help.bindings, binding => binding.description), ['page save']);
  const modal = new ElementStub(); r.document.modals = [modal];
  r.add({ key: 'Enter', description: 'Confirm' }, () => {}, { scope: 'dialog', dialogRef: { current: modal } });
  r.api.openHelp(); help = r.render().props.children.at(-1).props;
  assert.deepEqual(Array.from(help.bindings, binding => binding.description), ['Confirm']);
});

test('useHotkeys keeps latest handlers and options without duplicate subscriptions', () => {
  const r = registry(); const hooks = hookHarness(); hooks.setContext(r.api);
  const hotkeys = load('hotkeys/useHotkey.ts', { react: hooks.react, './HotkeyContext': { HotkeyContext: r.context } }, r.globals);
  const calls = [];
  hooks.render(() => hotkeys.useHotkey({ key: 's' }, () => calls.push('old'), { enabled: true }));
  hooks.render(() => hotkeys.useHotkey({ key: 's' }, () => calls.push('new'), { enabled: true }));
  r.dispatch('s');
  hooks.render(() => hotkeys.useHotkey({ key: 's' }, () => calls.push('disabled'), { enabled: false }));
  r.dispatch('s');
  assert.deepEqual(calls, ['new']);
});

function confirmDialog(props) {
  const r = registry(); const hooks = hookHarness(); hooks.setContext(r.api);
  const hotkeys = load('hotkeys/useHotkey.ts', { react: hooks.react, './HotkeyContext': { HotkeyContext: r.context } }, r.globals);
  const components = Object.fromEntries(['Button', 'CircularProgress', 'Dialog', 'DialogActions', 'DialogContent', 'DialogContentText', 'DialogTitle'].map(name => [name, name]));
  const Confirm = load('components/ConfirmDialog.tsx', { react: hooks.react, 'react/jsx-runtime': jsxRuntime,
    '@mui/material': components, '../hotkeys/useHotkey': hotkeys }, r.globals).default;
  const render = current => hooks.render(() => Confirm({ open: true, title: 'Delete', message: 'Delete this item?', onClose() {}, ...props, ...current }));
  const tree = render();
  const modal = new ElementStub(); tree.props.ref.current = modal; r.document.modals = [modal];
  return { ...r, tree, render };
}

test('ConfirmDialog focuses Confirm and accepts Enter once while an async action is pending', async () => {
  let resolve; const pending = new Promise(done => { resolve = done; }); let calls = 0;
  const r = confirmDialog({ onConfirm: () => { calls++; return pending; } });
  const confirm = r.tree.props.children.at(-1).props.children.at(-1);
  assert.ok(confirm.props.autoFocus);
  r.dispatch('Enter'); r.dispatch('Enter');
  assert.equal(calls, 1);
  resolve(); await pending; await new Promise(done => setImmediate(done));
  r.dispatch('Enter'); assert.equal(calls, 2);
});

test('ConfirmDialog ignores Enter while loading and ignores destructive key repeats', () => {
  let calls = 0;
  const r = confirmDialog({ loading: true, onConfirm: () => { calls++; } });
  r.dispatch('Enter');
  r.render({ loading: false }); r.dispatch('Enter', { repeat: true });
  assert.equal(calls, 0);
});

test('HotkeyHelpDialog groups scopes, labels keys, and closes with question mark', () => {
  const r = registry(); const hooks = hookHarness(); hooks.setContext(r.api);
  const hotkeys = load('hotkeys/useHotkey.ts', { react: hooks.react, './HotkeyContext': { HotkeyContext: r.context } }, r.globals);
  const components = Object.fromEntries(['Box', 'Button', 'Dialog', 'DialogActions', 'DialogContent', 'DialogTitle', 'List', 'ListItem', 'Typography'].map(name => [name, name]));
  const Help = load('hotkeys/HotkeyHelpDialog.tsx', { react: hooks.react, 'react/jsx-runtime': jsxRuntime,
    '@mui/material': components, './useHotkey': hotkeys, './keymap': r.keymap }, r.globals).default;
  let closes = 0;
  const tree = hooks.render(() => Help({ open: true, bindings: [
    { key: 's', scope: 'global', description: 'Select mode' },
    { key: 'Delete', shift: true, scope: 'page', description: 'Remove records' },
    { key: 'Enter', scope: 'dialog', description: 'Confirm' },
  ], onClose: () => { closes++; } }));
  const modal = new ElementStub(); tree.props.ref.current = modal; r.document.modals = [modal];
  const groups = tree.props.children[1].props.children[1];
  assert.deepEqual(Array.from(groups, group => group.props.children[0].props.children), ['dialog', 'page', 'global']);
  const keyLabels = Array.from(groups, group => group.props.children[1].props.children[0].props.children[1].props.children);
  assert.deepEqual(keyLabels, ['ENTER', 'Shift+Del', 'S']);
  r.dispatch('?', { shiftKey: true });
  assert.equal(closes, 1);
});


test('dialog menu opt-in preserves the owning modal even while MUI marks it aria-hidden', () => {
  const r = registry();
  const dialog = new ElementStub({ ariaHidden: true });
  r.document.modals = [dialog];
  r.document.menuOpen = true;
  let calls = 0;
  r.add({ key: 's' }, () => { calls++; }, { scope: 'dialog', dialogRef: { current: dialog }, allowInMenu: true });
  r.dispatch('s');
  assert.equal(calls, 1);
});
