const assert = require('node:assert/strict');
const test = require('node:test');
const { hooks, load, component, settle } = require('./detailRuntime.cjs');
const { runtime, load: datasetLoad, nodes, flush } = require('./datasetRuntime.cjs');

function router(initial = '', state = { navigationContext: { ids: [7, 8] } }) {
  const history = [{ params: new URLSearchParams(initial), state }];
  let index = 0;
  const calls = [];
  const api = {
    useLocation: () => ({ pathname: '/photos', state: history[index].state }),
    useSearchParams: () => [history[index].params, (params, options) => {
      calls.push(options);
      const entry = { params, state: options.state };
      if (options.replace) history[index] = entry;
      else { history.splice(++index); history.push(entry); }
    }],
    useParams: () => ({ id: '1' }), useNavigate: () => () => {},
  };
  return { api, calls, current: () => history[index], back: () => index--, forward: () => index++, size: () => history.length };
}

test('the real editor URL hook pushes open/close, replaces options, preserves viewer state and follows Back/Forward', () => {
  const rt = hooks(), r = router('folder=photos');
  const { useEditorUrlState } = load('hooks/useEditorUrlState.ts', rt, { 'react-router-dom': r.api });
  const render = () => rt.render(useEditorUrlState);
  render().patch({ edit: '7' }, false);
  render().patch({ compare: '1' });
  render().patch({ guides: 'bucket,face' });
  assert.equal(r.size(), 2);
  assert.equal(r.current().params.get('folder'), 'photos');
  assert.deepEqual(r.current().state, { navigationContext: { ids: [7, 8] } });
  r.back(); assert.equal(render().params.has('edit'), false);
  r.forward(); assert.equal(render().params.get('compare'), '1');
  render().patch({ edit: null }, false);
  assert.equal(r.size(), 3);
  assert.equal(render().params.toString(), 'folder=photos');
  assert.deepEqual(r.calls.map(call => call.replace), [false, true, true, false]);
  rt.unmount();
});

test('consecutive option writes merge instead of dropping a tool or preset', () => {
  const rt = hooks(), r = router('edit=7');
  const { useEditorUrlState } = load('hooks/useEditorUrlState.ts', rt, { 'react-router-dom': r.api });
  const { patch } = rt.render(useEditorUrlState);
  patch({ tool: 'crop' }); patch({ preset: 'portrait' });
  assert.equal(r.current().params.get('tool'), 'crop');
  assert.equal(r.current().params.get('preset'), 'portrait');
  rt.unmount();
});

function grid(rawId, getMedia) {
  const rt = hooks(), r = router(`edit=${rawId}`);
  const Grid = load('components/GridImageEditor.tsx', rt, {
    'react-router-dom': r.api, '../services/media': { getMedia },
  }).default;
  return { rt, r, render: () => rt.render(Grid) };
}
test('grid editor resolves a reload without any mounted media card and closes on Back', async () => {
  const h = grid('827', async () => ({ media: { id: 827, duration: null } }));
  h.render(); await settle();
  const editor = component(h.render(), 'LazyEditor');
  assert.equal(editor.props.media.id, 827);
  editor.props.onClose(); assert.equal(h.r.current().params.has('edit'), false);
  h.rt.unmount();
});
test('grid malformed, stale and video ids are stripped without rendering an editor', async () => {
  for (const [id, fetcher] of [
    ['bad', () => { throw Error('must not fetch'); }],
    ['827', async () => { throw Error('404'); }],
    ['828', async () => ({ media: { id: 828, duration: 3 } })],
  ]) {
    const h = grid(id, fetcher); h.render(); await settle();
    assert.equal(h.render(), null);
    assert.equal(h.r.current().params.has('edit'), false);
    h.rt.unmount();
  }
});
test('dataset crop reload resolves items beyond the first page and strips a missing item', async () => {
  for (const exists of [true, false]) {
    const rt = runtime(), r = router('crop=902');
    const services = {
      getDataset: async () => ({ id: 1, name: 'fixture', buckets: [512] }),
      getDatasetItems: async (_, cursor) => ({ items: cursor && exists ? [{ id: 902, dataset_id: 1, media: { id: 827 } }] : [], next_cursor: cursor ? null : 'second' }),
      getDatasetExports: async () => [], getTrainingRuns: async () => [], getDatasetAnalysis: async () => ({}), getDatasetGaps: async () => [],
    };
    const Page = datasetLoad('pages/DatasetDetailPage.tsx', rt, { 'react-router-dom': r.api, '../services/datasets': services }).default;
    const render = () => rt.render(Page);
    render(); await flush();
    const editors = nodes(render(), node => node.type === 'LazyEditor');
    assert.equal(editors.length, exists ? 1 : 0);
    assert.equal(r.current().params.has('crop'), exists);
    rt.unmount();
  }
});
