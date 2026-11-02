const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../src');
const pages = ['AlbumsPage', 'EventsPage', 'PlacesPage', 'TagPage', 'AlbumDetailPage', 'EventDetailPage', 'HighlightsPage', 'TagDetailPage', 'PersonDetailPage', 'OrphanFaces', 'PersonReviewPage', 'MissingFilesPage', 'BrokenMediaPage', 'SearchResultPage', 'DatasetsPage', 'DatasetDetailPage', 'CurationReviewPage', 'DuplicatesPage', 'PlaceMediaPage', 'IndexPage', 'BlurryPage', 'LowResolutionPage', 'NoExifDatePage', 'ShortVideosPage', 'UntaggedPage'];
for (const page of pages) test(`${page} uses the common gallery page shell`, () => {
  assert.ok(/<GalleryPage[\s>]/.test(fs.readFileSync(path.join(root, 'pages', `${page}.tsx`), 'utf8')), `${page} must render GalleryPage`);
});
for (const component of ['MediaListPage', 'PeopleGrid', 'OrphanFaceSuggestions', 'ReviewPage', 'MediaRelatedContent']) test(`${component} adapts to the common gallery page shell`, () => {
  assert.match(fs.readFileSync(path.join(root, 'components', `${component}.tsx`), 'utf8'), /<GalleryPage[\s>]/);
});
for (const [page, adapter] of [['ImagesPage', 'MediaListPage'], ['VideosPage', 'MediaListPage'], ['FavoritesPage', 'MediaListPage'], ['PeoplePage', 'PeopleGrid'], ['HiddenPeoplePage', 'PeopleGrid']]) test(`${page} uses the common ${adapter} adapter`, () => {
  assert.match(fs.readFileSync(path.join(root, 'pages', `${page}.tsx`), 'utf8'), new RegExp(`<${adapter}[\\s>]`));
});

for (const [page, kind] of [['NofacesPage', 'nofaces'], ['NopersonsPage', 'nopersons']]) test(`${page} uses the shared person review gallery adapter`, () => {
  assert.match(fs.readFileSync(path.join(root, 'pages', `${page}.tsx`), 'utf8'), new RegExp(`<PersonReviewPage kind="${kind}"`));
});

test('duplicates has one window scroll surface and keeps full loaded selection order', () => {
  const source = require('node:fs').readFileSync(require('node:path').join(__dirname, '../src/pages/DuplicatesPage.tsx'), 'utf8');
  assert.doesNotMatch(source, /react-window|VariableSizeList|viewport\.height/);
  assert.match(source, /useWindowVirtualRows/);
  assert.match(source, /orderedIds:/);
  assert.match(source, /loadedCount:/);
  assert.match(source, /infiniteScrollObserver/);
});
