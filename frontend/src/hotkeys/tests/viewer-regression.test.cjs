/* eslint-disable @typescript-eslint/no-require-imports -- CommonJS harness uses Node built-ins and the installed TypeScript compiler. */
// Run from the repository root: node frontend/src/hotkeys/tests/viewer-regression.test.cjs
// Exercise the actual TSX component callbacks with a small React-hook harness.
// Services and DOM modal identity are stubbed; no server or browser is required.
// Before the fixes: both delete tests sent 202 instead of the captured 101,
// the child-modal test navigated once, and tab panels had no accessible labels.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../../../..');
const ts = require(path.join(root, 'frontend/node_modules/typescript'));
const component = (name) => Object.assign(() => null, {displayName: name});
const jsx = (type, props) => ({type, props: props || {}});
function harness(relative, overrides = {}) {
  let cursor = 0;
  const state = [];
  const deleted = [], removed = [], navigations = [], hotkeys = [];
  const viewerRoot = {getAttribute: () => null, contains: () => false};
  let childOpen = false;
  const document = {
    querySelectorAll: () => childOpen ? [viewerRoot, {getAttribute: () => null}] : [viewerRoot],
    querySelector: () => childOpen ? {getAttribute: () => null} : viewerRoot,
  };
  const media = (id) => ({id, filename: `${id}.jpg`, tags: [], is_favorite: false});
  const location = {state: {media: media(101), navigationContext: {ids: [101, 202]}}, key: 'one'};
  let routeId = '101';
  const react = {
    createElement: (type, props, ...children) => jsx(type, {...props, children}),
    lazy: () => component('Lazy'), Suspense: component('Suspense'),
    useState: (initial) => { const i = cursor++; if (!(i in state)) state[i] = typeof initial === 'function' ? initial() : initial; return [state[i], (v) => state[i] = typeof v === 'function' ? v(state[i]) : v]; },
    useRef: (initial) => { const i = cursor++; if (!(i in state)) state[i] = {current: initial}; return state[i]; },
    useEffect: () => {}, useMemo: (f) => f(), useCallback: (f) => f, useId: () => 'test-tabs',
  };
  const store = {lists: {}, updateItem: () => {}, removeItem: (...args) => removed.push(args)};
  const ui = new Proxy({useTheme: () => ({breakpoints: {down: () => false}}), useMediaQuery: () => false}, {get: (target, key) => target[key] || component(key)});
  const stubs = {
    react: {__esModule: true, default: react, ...react},
    'react/jsx-runtime': {jsx, jsxs: jsx},
    'react-router-dom': {useParams: () => ({id: routeId}), useLocation: () => location, useNavigate: () => (...args) => navigations.push(args)},
    '@mui/material': ui,
    '../stores/useListStore': {useListStore: (selector) => selector ? selector(store) : store},
    '../context/UndoContext': {useUndoRefresh: () => {}},
    '../TaskEventsContext': {useTaskCompletionVersion: () => 0},
    '../config': {__esModule: true, default: {PRESENTATION_MODE: false}},
    '../services/mediaActions': {deleteMediaFile: async (id) => deleted.push(id), deleteMediaRecord: async (id) => deleted.push(id)},
    '../hotkeys/keymap': {getTopModal: () => childOpen ? {} : viewerRoot},
    '../hotkeys/useHotkey': {useHotkeyHelp: () => () => {}, useDialogHotkeyScope: () => ({current: viewerRoot}), useHotkey: (binding, handler, options) => hotkeys.push({binding, handler, options}), useHotkeys: (bindings, handler, options) => hotkeys.push({bindings, handler, options})},
    ...overrides,
  };
  const code = ts.transpileModule(fs.readFileSync(path.join(root, relative), 'utf8'), {compilerOptions: {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true}}).outputText;
  const module = {exports: {}};
  const requireStub = (id) => stubs[id] || {__esModule: true, default: component(id.split('/').pop()), [id.split('/').pop()]: component(id.split('/').pop())};
  new Function('require', 'module', 'exports', 'document', code)(requireStub, module, module.exports, document);
  return {render(props) {cursor = 0; hotkeys.length = 0; return (module.exports.default || module.exports.MediaContentTabs || module.exports.SelectionActionBar)(props);}, state, media, deleted, removed, navigations, hotkeys, location, changeRoute(id) {routeId = String(id);}, openChild() {childOpen = true;}};
}
const nodes = (node) => node && typeof node === 'object' ? [node, ...[node.props?.children].flat(Infinity).flatMap(nodes)] : [];
const name = (node) => node.type?.displayName || node.type;
const find = (tree, componentName, predicate = () => true) => nodes(tree).find((n) => name(n) === componentName && predicate(n.props));
async function testMenuSnapshot(deleteFile = true) {
  const h = harness('frontend/src/components/MediaCardMenu.tsx');
  const callbacks = [];
  const props = (id) => ({media: h.media(id), mediaListKey: `test-list-${id}`, onDeleted: () => callbacks.push(id)});
  const tree = h.render(props(101));
  find(tree, 'MenuItem', (p) => p.children.includes(deleteFile ? 'Delete file…' : 'Delete record…')).props.onClick();
  const changed = h.render(props(202));
  find(changed, 'ConfirmDialog', (p) => p.title === (deleteFile ? 'Delete File from Disk?' : 'Remove from Library?')).props.onConfirm();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(h.deleted, [101], 'delete must use media present when confirm opened');
  assert.deepEqual(h.removed, [['test-list-101', 101]], 'cache removal must use captured media and list');
  assert.deepEqual(callbacks, [101], 'completion must use callback captured for the deleted media');
}
async function testViewerSnapshot(deleteFile = true) {
  const h = harness('frontend/src/pages/MediaDetailPage.tsx');
  h.location.state.mediaListKey = 'test-list-101';
  const tree = h.render();
  find(tree, 'MediaHeader').props.onOpenDialog(deleteFile ? 'deleteFile' : 'deleteRecord');
  h.location.state.mediaListKey = 'test-list-202';
  h.state[h.state.findIndex((value) => value?.media?.id === 101)] = {media: h.media(202), persons: [], orphans: []};
  h.changeRoute(202);
  const changed = h.render();
  await find(changed, 'ActionDialogs').props[deleteFile ? 'onConfirmDeleteFile' : 'onConfirmDeleteRecord']();
  assert.deepEqual(h.deleted, [101], 'viewer delete must use captured media after route change');
  assert.deepEqual(h.removed, [['test-list-101', 101]], 'viewer cache removal must use captured media and list');
}
async function testTabPanelLabels() {
  const h = harness('frontend/src/components/MediaContentTabs.tsx');
  const tree = h.render({detail: {media: h.media(101), persons: [], orphans: []}, tabKey: 'tags'});
  const tabs = nodes(tree).filter((node) => name(node) === 'Tab');
  const panels = nodes(tree).filter((node) => typeof node.type === 'function' && node.type.name === 'TabPanel').map((node) => node.type(node.props));
  assert.ok(tabs.length > 0 && panels.length > 0);
  for (const panel of panels) {
    const tab = tabs.find((node) => node.props.id && node.props.id === panel.props['aria-labelledby']);
    assert.ok(tab, 'each media panel must be labelled by its tab');
    assert.equal(tab.props['aria-controls'], panel.props.id, 'each media tab must control its panel');
  }
}
async function testChildNavigationLock() {
  const h = harness('frontend/src/pages/MediaDetailPage.tsx');
  const tree = h.render();
  h.openChild();
  const next = find(tree, 'IconButton', (p) => nodes(p.children).some((n) => name(n) === 'ArrowForwardIos'));
  next.props.onClick();
  assert.equal(h.navigations.length, 0, 'viewer must not navigate while child modal is open');
}
module.exports = { harness, nodes, name, find };

// Other component suites reuse the hook harness without registering these tests.
if (require.main === module) {
  for (const deleteFile of [true, false]) {
    const action = deleteFile ? 'file deletion' : 'record removal';
    test(`card ${action} captures the media id, list key and completion callback`, () => testMenuSnapshot(deleteFile));
    test(`viewer ${action} keeps its target after a route change`, () => testViewerSnapshot(deleteFile));
  }
  test('viewer navigation stops while a nested modal is open', testChildNavigationLock);
  test('every media tab controls a labelled tab panel', testTabPanelLabels);
}
