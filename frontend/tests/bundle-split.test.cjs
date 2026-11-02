const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const root = path.resolve(__dirname, '../src');
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
function eagerImports(file) {
  const source = ts.createSourceFile(file, read(file), ts.ScriptTarget.Latest, true);
  return source.statements.filter(s =>
    (ts.isImportDeclaration(s) && !s.importClause?.isTypeOnly &&
      (!s.importClause?.namedBindings || !ts.isNamedImports(s.importClause.namedBindings) ||
        s.importClause.name || s.importClause.namedBindings.elements.some(e => !e.isTypeOnly))) ||
    (ts.isExportDeclaration(s) && !s.isTypeOnly && s.moduleSpecifier))
    .map(s => s.moduleSpecifier.text);
}
function closure(start, seen = new Set()) {
  if (seen.has(start)) return seen;
  seen.add(start);
  for (const id of eagerImports(start)) {
    assert.doesNotMatch(id, /(?:ImageEditorDialog|PersonRelationshipGraph|react-filerobot-image-editor|@antv\/g6)/,
      `${start} eagerly imports ${id}`);
    if (!id.startsWith('.')) continue;
    const base = path.resolve(root, path.dirname(start), id);
    const resolved = ['.tsx', '.ts', '/index.tsx', '/index.ts'].map(ext => base + ext).find(fs.existsSync);
    if (resolved) closure(path.relative(root, resolved), seen);
  }
  return seen;
}
test('entry, shared UI and route chunks have no eager editor/graph import chain', () => {
  for (const start of ['routes.tsx', 'pages/MediaDetailPage.tsx', 'pages/DatasetDetailPage.tsx', 'pages/PersonDetailPage.tsx']) closure(start);
});
test('editor lazy components are gated on actual open intent with loading feedback', () => {
  assert.match(read('pages/MediaDetailPage.tsx'), /editorOpen &&[\s\S]*?<Suspense fallback=\{<CircularProgress/);
  assert.match(read('pages/DatasetDetailPage.tsx'), /cropItem && <Suspense fallback=\{<CircularProgress/);
  assert.match(read('components/GridImageEditor.tsx'), /if \(id === null \|\| !media \|\| media.id !== id\) return null/);
  assert.match(read('components/GridImageEditor.tsx'), /Loading image editor/);
  assert.ok(!eagerImports('components/ImageEditorDialog.tsx').includes('react-filerobot-image-editor'));
  assert.match(read('components/ImageEditorDialog.tsx'), /lazy\(async \(\) => \{\s*const editorModule = await import\("react-filerobot-image-editor"\)/);
});
test('inactive person tabs defer their components', () => {
  assert.match(read('components/PersonContentTabs.tsx'), /value === index &&/);
  for (const id of ['TimelineTab', 'SimilarPersonCard', 'TagsSection']) {
    assert.ok(!eagerImports('components/PersonContentTabs.tsx').includes(`./${id}`), `${id} is eager`);
    assert.ok(read('components/PersonContentTabs.tsx').includes(`import("./${id}")`));
  }
});
test('graph uses the narrow G6 runtime without the all-extensions preset', () => {
  assert.ok(!eagerImports('components/PersonRelationshipGraph.tsx').includes('@antv/g6'), 'G6 root registers unused extensions');
});
test('narrow graph preset retains upstream core defaults and the configured behaviors', () => {
  const registrations = [];
  const source = read('components/personGraphExtensions.ts');
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  new Function('require', 'exports', js)(id => id.endsWith('/registry/register')
    ? { register: (category, name) => registrations.push(`${category}:${name}`) }
    : new Proxy({}, { get: (_, key) => key }), {});
  const upstream = fs.readFileSync(require.resolve('@antv/g6/esm/runtime/transform.js'), 'utf8');
  const requiredTransforms = [...upstream.match(/REQUIRED_TRANSFORMS = \[([\s\S]*?)\]/)[1].matchAll(/'([^']+)'/g)].map(m => m[1]);
  const graph = read('components/PersonRelationshipGraph.tsx');
  const behaviors = [...graph.match(/behaviors: \[([^\]]+)\]/)[1].matchAll(/"([^"]+)"/g)].map(m => m[1]);
  for (const name of requiredTransforms) assert.ok(registrations.includes(`transform:${name}`), name);
  for (const name of behaviors) assert.ok(registrations.includes(`behavior:${name}`), name);
  for (const name of ['node:circle', 'edge:quadratic', 'theme:light', 'theme:dark',
    'shape:circle', 'shape:image', 'shape:label', 'shape:badge',
    'animation:fade', 'animation:translate', 'animation:node-collapse', 'animation:node-expand',
    'animation:path-in', 'animation:path-out', 'animation:combo-collapse', 'animation:combo-expand']) {
    assert.ok(registrations.includes(name), name);
  }
  assert.equal(registrations.some(name => name.startsWith('plugin:') || name.startsWith('layout:')), false);
});
