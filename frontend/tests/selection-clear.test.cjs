const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const read = (p) => fs.readFileSync(path.join(__dirname, '../src', p), 'utf8');

test('the shared selection toolbar renders a clear button and binds Escape when items are selected', () => {
  const code = read('components/BulkResolveToolbar.tsx');
  assert.match(code, /onClearSelection\?: \(\) => void/);
  assert.match(code, /Clear \(\{selectedCount\}\)/);
  assert.match(code, /useHotkey\(\{ key: "Escape" \}, \(\) => onClearSelection\?\.\(\)/);
});

test('every selection toolbar page passes a clear handler', () => {
  for (const file of ['pages/OrphanFaces.tsx', 'components/FaceReviewViews.tsx', 'components/PeopleGrid.tsx', 'pages/DatasetDetailPage.tsx']) {
    assert.match(read(file), /<StickySelectionToolbar[^>]*onClearSelection=/, file);
  }
});
