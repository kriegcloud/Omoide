/* eslint-disable @typescript-eslint/no-require-imports -- Standalone Node test executes the real TypeScript grid adapter with a small DOM harness. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');

const filename = path.resolve(__dirname, '../GridKeyboardNavigation.tsx');
const source = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
} }).outputText;

function fixture() {
  const observers = [];
  const listeners = new Map();
  const registrations = [];
  const cleanups = [];
  let document;
  class ElementStub {
    constructor({ parent = null, tag = 'div', attributes = [], classes = [], top = 0, left = 0, visible = true } = {}) {
      Object.assign(this, { tag, attributes: new Set(attributes), classes: new Set(classes), top, left, visible, parentElement: parent, children: [], tabIndex: -1 });
      parent?.children.push(this);
    }
    get isConnected() { return document.body === this || !!this.parentElement?.isConnected; }
    matches(selector) {
      return selector.split(',').some(part => {
        const trimmed = part.trim();
        if (trimmed.startsWith('.')) return this.classes.has(trimmed.slice(1));
        return trimmed.startsWith('[') ? this.attributes.has(trimmed.slice(1, -1)) : this.tag === trimmed;
      });
    }
    closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) ?? null; }
    hasAttribute(attribute) { return this.attributes.has(attribute); }
    contains(other) { for (let current = other; current; current = current.parentElement) if (current === this) return true; return false; }
    querySelectorAll(selector) {
      return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]);
    }
    getClientRects() { return this.visible && this.isConnected ? [this.getBoundingClientRect()] : []; }
    getBoundingClientRect() { return { top: this.top, left: this.left, width: 100, height: 100 }; }
    focus() { document.activeElement = this; for (const listener of listeners.get('focusin') ?? []) listener({ target: this }); }
    remove() {
      const parent = this.parentElement;
      if (this.contains(document.activeElement)) document.activeElement = document.body;
      parent.children = parent.children.filter(child => child !== this);
      this.parentElement = null;
      document.mutate(parent);
    }
  }
  class ObserverStub {
    constructor(callback) { this.callback = callback; this.targets = []; observers.push(this); }
    observe(target, options) { this.targets.push({ target, options }); }
    disconnect() { this.targets = []; }
  }
  document = {
    modal: null, menuOpen: false,
    addEventListener(type, listener) { listeners.set(type, [...(listeners.get(type) ?? []), listener]); },
    removeEventListener(type, listener) { listeners.set(type, (listeners.get(type) ?? []).filter(item => item !== listener)); },
    mutate(target) {
      for (const observer of [...observers]) {
        if (observer.targets.some(entry => entry.target === target || entry.options.subtree && entry.target.contains(target))) observer.callback([{ target, type: 'childList' }]);
      }
    },
  };
  document.body = new ElementStub({ tag: 'body' }); document.activeElement = document.body;
  const exports = {};
  vm.runInNewContext(source, { exports, document, Element: ElementStub, HTMLElement: ElementStub, MutationObserver: ObserverStub,
    require: name => {
      if (name === 'react') return { useEffect: effect => { cleanups.push(effect()); } };
      if (name === './useHotkey') return { useHotkeys: (bindings, handler, options) => registrations.push({ bindings, handler, options }) };
      if (name === './keymap') return { getTopModal: () => document.modal, isMenuOpen: () => document.menuOpen,
        gridBindings: ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].map(key => ({ key })) };
      throw new Error(`Unexpected import: ${name}`);
    },
  }, { filename });
  exports.GridKeyboardNavigation();
  const grid = new ElementStub({ parent: document.body, attributes: ['data-selection-grid'] });
  const tile = (attributes = {}) => new ElementStub({ parent: grid, attributes: ['data-selectable-id'], ...attributes });
  return { ...exports, document, grid, tile, ElementStub, registrations,
    arrow(key, target) { return registrations[0].handler({ key, target }); },
    cleanup() { cleanups.forEach(cleanup => cleanup?.()); },
  };
}

const positions = (coordinates) => coordinates.map(([top, left]) => ({ top, left }));

test('arrows follow visual row order in a column-major masonry DOM', () => {
  const { nextGridIndex } = fixture();
  const layout = positions([[0, 0], [100, 0], [200, 0], [0, 100], [100, 100], [200, 100]]);
  assert.equal(nextGridIndex(layout, 0, 'ArrowRight'), 3);
  assert.equal(nextGridIndex(layout, 3, 'ArrowRight'), 1);
  assert.equal(nextGridIndex(layout, 1, 'ArrowLeft'), 3);
  assert.equal(nextGridIndex(layout, 3, 'ArrowDown'), 4);
  assert.equal(nextGridIndex(layout, 4, 'ArrowUp'), 3);
});

test('row alignment tolerates subpixel layout offsets and clamps short rows', () => {
  const { nextGridIndex } = fixture();
  const layout = positions([[0, 0], [2, 100], [1, 200], [100, 0], [101, 100], [200, 0]]);
  assert.equal(nextGridIndex(layout, 0, 'ArrowRight'), 1);
  assert.equal(nextGridIndex(layout, 2, 'ArrowDown'), 4);
  assert.equal(nextGridIndex(layout, 4, 'ArrowDown'), 5);
  assert.equal(nextGridIndex(layout, 5, 'ArrowDown'), 5);
  assert.equal(nextGridIndex(layout, 0, 'ArrowLeft'), 0);
  assert.equal(nextGridIndex(layout, 0, 'ArrowUp'), 0);
});

test('empty grids and missing focused tiles have no next index', () => {
  const { nextGridIndex } = fixture();
  assert.equal(nextGridIndex([], -1, 'ArrowRight'), -1);
  assert.equal(nextGridIndex(positions([[0, 0]]), -1, 'ArrowDown'), -1);
});

test('media arrows focus visual neighbors and establish one tab stop', () => {
  const f = fixture();
  const first = f.tile({ top: 0, left: 0 }); const below = f.tile({ top: 100, left: 0 });
  const right = f.tile({ top: 0, left: 100 });
  first.focus(); f.arrow('ArrowRight', first);
  assert.equal(f.document.activeElement, right);
  assert.deepEqual([first.tabIndex, below.tabIndex, right.tabIndex], [-1, -1, 0]);
});

test('roving-marked tiles still receive fallback when no page handler owns them', () => {
  const f = fixture();
  const marked = f.tile({ attributes: ['data-selectable-id', 'data-roving-tile'] });
  const media = f.tile({ left: 100 });
  marked.focus(); f.arrow('ArrowRight', marked);
  assert.equal(f.document.activeElement, media);
});

test('nested controls retain their own arrow handlers', () => {
  const f = fixture(); const media = f.tile();
  const button = new f.ElementStub({ parent: media, tag: 'button' });
  button.focus();
  assert.equal(f.arrow('ArrowRight', button), false);
  assert.equal(f.document.activeElement, button);
});

test('navigation excludes hidden tiles and members of nested grids', () => {
  const f = fixture(); const first = f.tile();
  f.tile({ left: 50, visible: false });
  const nested = new f.ElementStub({ parent: f.grid, attributes: ['data-selection-grid'] });
  f.tile({ parent: nested, left: 70 });
  const next = f.tile({ left: 100 });
  first.focus(); f.arrow('ArrowRight', first);
  assert.equal(f.document.activeElement, next);
});

test('removing the focused tile hands focus to its replacement index', () => {
  const f = fixture(); const first = f.tile(); const removed = f.tile({ left: 100 }); const next = f.tile({ left: 200 });
  removed.focus(); removed.remove();
  assert.equal(f.document.activeElement, next);
  assert.equal(next.tabIndex, 0);
  assert.equal(first.tabIndex, -1);
});

test('removing the final tile uses the previous tile and an empty grid stays unfocused', () => {
  const f = fixture(); const first = f.tile(); const removed = f.tile({ left: 100 });
  removed.focus(); removed.remove(); assert.equal(f.document.activeElement, first);
  first.remove(); assert.equal(f.document.activeElement, f.document.body);
});

test('focus handoff does not steal focus from another action or a disconnected grid', () => {
  const f = fixture(); const removed = f.tile(); f.tile({ left: 100 });
  const action = new f.ElementStub({ parent: f.document.body, tag: 'button' });
  removed.focus(); action.focus(); removed.remove();
  assert.equal(f.document.activeElement, action);
  const remaining = f.grid.children[0]; remaining.focus(); f.grid.remove();
  assert.equal(f.document.activeElement, f.document.body);
});

test('focus handoff waits for confirmation to close after the selected tile is removed', () => {
  const f = fixture(); const removed = f.tile(); const next = f.tile({ left: 100 });
  removed.focus();
  const modal = new f.ElementStub({ parent: f.document.body }); f.document.modal = modal;
  const confirm = new f.ElementStub({ parent: modal, tag: 'button' }); confirm.focus();
  removed.remove();
  assert.equal(f.document.activeElement, confirm, 'background removal must not move focus out of the modal');
  f.document.modal = null; modal.remove();
  assert.equal(f.document.activeElement, next, 'closing the portal must retry handoff even without another grid mutation');
});

test('unmount cleanup removes focus listeners and mutation observers', () => {
  const f = fixture(); const removed = f.tile(); f.tile({ left: 100 });
  removed.focus(); f.cleanup(); removed.remove();
  assert.equal(f.document.activeElement, f.document.body);
});

test('focus handoff waits until a hidden modal actually releases focus', () => {
  const f = fixture(); const removed = f.tile(); const next = f.tile({ left: 100 });
  removed.focus();
  const modal = new f.ElementStub({ parent: f.document.body, classes: ['MuiModal-root'] }); f.document.modal = modal;
  const confirm = new f.ElementStub({ parent: modal, tag: 'button' }); confirm.focus();
  removed.remove();
  f.document.modal = null; f.document.mutate(modal);
  assert.equal(f.document.activeElement, confirm, 'exit transitions must finish before focus is moved');
  modal.remove();
  assert.equal(f.document.activeElement, next);
});

test('focus handoff also resumes after a menu closes', () => {
  const f = fixture(); const removed = f.tile(); const next = f.tile({ left: 100 });
  removed.focus();
  const menu = new f.ElementStub({ parent: f.document.body }); f.document.menuOpen = true;
  const action = new f.ElementStub({ parent: menu, tag: 'button' }); action.focus();
  removed.remove(); assert.equal(f.document.activeElement, action);
  f.document.menuOpen = false; menu.remove();
  assert.equal(f.document.activeElement, next);
});

test('a deliberate focus change cancels deferred portal-close handoff', () => {
  const f = fixture(); const removed = f.tile(); f.tile({ left: 100 });
  removed.focus();
  const modal = new f.ElementStub({ parent: f.document.body }); f.document.modal = modal;
  removed.remove();
  const action = new f.ElementStub({ parent: f.document.body, tag: 'button' });
  f.document.modal = null; action.focus(); modal.remove();
  assert.equal(f.document.activeElement, action);
  action.remove();
  assert.equal(f.document.activeElement, f.document.body, 'later removal of the new focus owner must not resurrect a cancelled handoff');
});

test('unmount cancels the temporary observer waiting on a dialog portal', () => {
  const f = fixture(); const removed = f.tile(); f.tile({ left: 100 });
  removed.focus();
  const modal = new f.ElementStub({ parent: f.document.body }); f.document.modal = modal;
  removed.remove(); f.cleanup(); f.document.modal = null; modal.remove();
  assert.equal(f.document.activeElement, f.document.body);
});
