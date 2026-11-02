const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const material = require('@mui/material');
const filename = path.resolve(__dirname, '../src/theme.ts');
const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const mod = { exports: {} };
new Function('require', 'module', 'exports', code)(name => {
  assert.equal(name, '@mui/material');
  return material;
}, mod, mod.exports);
for (const mode of ['light', 'dark']) {
  test(`${mode} scrollbars reserve space and reveal themed thumbs on each hovered/focused scroll owner`, () => {
    const theme = mod.exports.getTheme(mode);
    const css = theme.components.MuiCssBaseline.styleOverrides(theme);
    const base = css['*, html, body'];
    assert.equal(base.scrollbarWidth, 'thin');
    assert.equal(base.scrollbarColor, 'transparent transparent');
    assert.equal(base.scrollbarGutter, 'stable');
    const reveal = css['*:hover, *:focus-within, html:hover, html:focus-within, body:hover, body:focus-within'];
    assert.equal(reveal.scrollbarColor, `${material.alpha(theme.palette.text.primary, 0.25)} transparent`);
    assert.deepEqual(css['*::-webkit-scrollbar'], { width: '8px', height: '8px' });
    assert.equal(css['*::-webkit-scrollbar-thumb'].backgroundColor, 'transparent');
    assert.equal(css['*::-webkit-scrollbar-thumb'].borderRadius, '8px');
    assert.equal(css['*:hover::-webkit-scrollbar-thumb, *:focus-within::-webkit-scrollbar-thumb'].backgroundColor, material.alpha(theme.palette.text.primary, 0.25));
    assert.equal(css['*::-webkit-scrollbar-thumb:hover'].backgroundColor, material.alpha(theme.palette.text.primary, 0.4));
  });
}
