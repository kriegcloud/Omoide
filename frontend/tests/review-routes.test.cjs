const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const source = name => fs.readFileSync(path.join(__dirname, '../src', name), 'utf8');
test('review routes resolve separate no-face, no-people and watermark pages', () => {
  const routes = source('routes.tsx');
  for (const [route, page] of [['nofaces', 'NofacesPage'], ['nopersons', 'NopersonsPage'], ['watermarks', 'WatermarksPage']]) {
    assert.match(routes, new RegExp(`path="/${route}"[\\s\\S]*?<${page} />`));
    assert.ok(routes.includes(`import("./pages/${page}")`));
  }
});
test('Review navigation distinguishes no faces, no people and watermarks', () => {
  const sidebar = source('components/Sidebar.tsx');
  assert.match(sidebar, /label: "Review"/);
  for (const [label, route] of [['No faces', 'nofaces'], ['No people', 'nopersons'], ['Watermarks', 'watermarks']]) {
    assert.match(sidebar, new RegExp(`label: "${label}", to: "/${route}"`));
  }
});
test('watermark candidate overlays use their own entity identity', () => {
  const ts = require('typescript');
  const module = { exports: {} };
  const code = ts.transpileModule(source('stores/queryKeys.ts'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  new Function('exports', code)(module.exports);
  assert.equal(module.exports.queryEntityKind('watermarks?folder=photos&status=applied'), 'watermark');
  assert.equal(module.exports.queryEntityKind('nopersons?scope=processed'), 'media');
  assert.equal(module.exports.queryEntityKind('nofaces?scope=pending'), 'media');
});
