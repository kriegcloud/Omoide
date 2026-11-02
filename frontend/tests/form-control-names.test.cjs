// Static guard (review rounds 2-3): every MUI Slider, Checkbox and labelled Select
// must carry an accessible name; MUI's `label` prop on Select only sizes the outline.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

function* files(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* files(full);
    else if (full.endsWith('.tsx')) yield full;
  }
}
function openingTags(text, name) {
  const out = [];
  const re = new RegExp(`<${name}\\b`, 'g');
  let match;
  while ((match = re.exec(text))) {
    let depth = 0, j = match.index;
    for (; j < text.length; j++) {
      const c = text[j];
      if (c === '{') depth++; else if (c === '}') depth--; else if (c === '>' && depth === 0) break;
    }
    out.push({ index: match.index, tag: text.slice(match.index, j + 1) });
  }
  return out;
}
const named = /aria-label|aria-labelledby|getAriaLabel|inputProps|labelId=/;

test('form controls have accessible names', () => {
  const missing = [];
  const src = path.join(__dirname, '../src');
  for (const file of files(src)) {
    const text = fs.readFileSync(file, 'utf8');
    const where = index => `${path.relative(src, file)}:${text.slice(0, index).split('\n').length}`;
    for (const { index, tag } of openingTags(text, 'Slider')) if (!named.test(tag)) missing.push(`Slider ${where(index)}`);
    for (const { index, tag } of openingTags(text, 'Checkbox')) {
      const before = text.slice(Math.max(0, index - 80), index);
      if (!named.test(tag) && !/control=\{\s*$/.test(before)) missing.push(`Checkbox ${where(index)}`);
    }
    for (const { index, tag } of openingTags(text, 'Select')) if (/\blabel=/.test(tag) && !named.test(tag)) missing.push(`Select ${where(index)}`);
  }
  assert.deepEqual(missing, []);
});
