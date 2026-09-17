const assert = require('node:assert/strict');
const { test } = require('node:test');
const { runtime, load, nodes, button, deferred, flush } = require('./datasetRuntime.cjs');

const hash = 'a'.repeat(64);
const caption = { id: 'caption-1', text: 'Rights-clear geometric fixture.', sha256: 'b'.repeat(64) };
const proof = { id: 'credential-id', rawId: 'aWQ', type: 'public-key', clientExtensionResults: {}, response: { clientDataJSON: 'Y2xpZW50', authenticatorData: 'YXV0aA', signature: 'c2ln', userHandle: null } };

function harness({ operations = ['read', 'preview', 'review', 'enroll', 'export'], kind = 'human', clientOverrides = {}, passkeyOverrides = {} } = {}) {
  const rt = runtime();
  let route = 'dataset-1';
  const calls = [];
  const dataset = { id: route, name: 'Review fixture', revision: 1, policy_version: 'production-stills-v1',
    actor: { id: 'fixture-actor', kind, human_presence_verified: false, operations },
    sources: [{ id: 'source-1', label: 'Geometric fixture', sha256: hash, status: 'materialized' }],
    items: [{ artifact_id: 'artifact-1', source_id: 'source-1', sha256: hash, width: 16, height: 12,
      generative_ancestry: false, caption, review: null, eligible: false, blockers: ['acceptance_required'] }],
    exports: [{ id: 'export-1', dataset_id: route, status: 'blocked', snapshot_revision: 1,
      manifest_sha256: null, item_count: 1, error_code: 'source_unavailable' }],
    remaining_count: 1, blockers: ['item_review_incomplete'] };
  const client = { list: async () => [{ id: route, name: dataset.name, revision: 1 }],
    get: async () => dataset,
    authStatus: async () => ({ actor_kind: kind, enrolled: true, enrollment_available: false }),
    reviewOptions: async () => ({ challenge_id: 'challenge-1', public_key: {} }),
    review: async (...args) => { calls.push(['review', ...args]); return dataset; },
    resume: async (...args) => { calls.push(['resume', ...args]); return dataset.exports[0]; },
    ...clientOverrides };
  const service = load('services/curation.ts', rt);
  const passkeys = { ...load('services/curationPasskeys.ts', rt), confirmCurationPasskey: async () => proof, ...passkeyOverrides };
  const Page = load('pages/CurationReviewPage.tsx', rt, {
    '../services/curation': service,
    '../services/curationPasskeys': passkeys,
    '../config': { __esModule: true, default: { PRESENTATION_MODE: false }, API: '' },
    '../components/DerivativeReviewPanel': { __esModule: true, default: 'DerivativeReviewPanel' },
    '../components/ConfirmDialog': { __esModule: true, default: 'ConfirmDialog' },
    '../hotkeys/useHotkey': { useHotkeys() {} },
    'react-router-dom': { useParams: () => ({ id: route }), useNavigate: () => () => {} },
  }).CurationWorkspace;
  const render = () => rt.render(() => Page({ client, mode: 'production', onDisconnect() {} }));
  return { rt, calls, client, render, setRoute(value) { route = value; },
    async start() { render(); await flush(); return render(); },
    ready() { nodes(render(), node => node.type === 'DerivativeReviewPanel')[0].props.onReady(true); return render(); },
    confirm() { return nodes(render(), node => node.type === 'ConfirmDialog')[0].props.onConfirm(); } };
}

test('a metadata-only production grant cannot enable Retry this export', async () => {
  const h = harness({ kind: 'agent', operations: ['read'] });
  try {
    const tree = await h.start();
    assert.equal(nodes(tree, node => node.type === 'DerivativeReviewPanel').length, 0);
    assert.equal(button(tree, 'Export accepted stills').props.disabled, true);
    assert.equal(button(tree, 'Retry this export').props.disabled, true);
  } finally { h.rt.unmount(); }
});

test('the retry handler checks export permission independently of disabled button state', async () => {
  const h = harness({ kind: 'agent', operations: ['read'] });
  try {
    button(await h.start(), 'Retry this export').props.onClick();
    await flush();
    assert.deepEqual(h.calls, []);
  } finally { h.rt.unmount(); }
});

test('unmount after the native assertion starts prevents late proof submission', async () => {
  const assertion = deferred();
  let signal;
  const h = harness({ passkeyOverrides: { confirmCurationPasskey: (_options, value) => { signal = value; return assertion.promise; } } });
  await h.start();
  button(h.ready(), 'Accept image and caption').props.onClick();
  h.confirm();
  await flush();
  h.rt.unmount();
  assert.equal(signal.aborted, true);
  assertion.resolve(proof);
  await flush();
  assert.deepEqual(h.calls, []);
});

test('server rejection after native confirmation keeps the exact request and requires refresh', async () => {
  const assertion = deferred();
  let captured;
  const h = harness({ passkeyOverrides: { confirmCurationPasskey: () => assertion.promise },
    clientOverrides: { reviewOptions: async (_id, input) => { captured = structuredClone(input); return { challenge_id: 'challenge-1', public_key: {} }; } } });
  try {
    const service = load('services/curation.ts', runtime());
    h.client.review = async (_id, input) => {
      h.calls.push(['review', input]);
      throw new service.CurationError(409, 'revision_conflict');
    };
    await h.start();
    button(h.ready(), 'Accept image and caption').props.onClick();
    h.confirm();
    await flush();
    assertion.resolve(proof);
    await flush();
    assert.equal(h.calls.length, 1);
    assert.deepEqual(h.calls[0][1], { ...captured, presence: { challenge_id: 'challenge-1', credential: proof } });
    assert.equal(button(h.render(), 'Accept image and caption').props.disabled, true);
    h.confirm(); await flush();
    assert.equal(h.calls.length, 1);
  } finally { h.rt.unmount(); }
});

test('unmount during native enrollment prevents a late registration verification request', async () => {
  const enrollment = deferred();
  let signal;
  const h = harness({
    passkeyOverrides: { registerCurationPasskey: (_options, value) => { signal = value; return enrollment.promise; } },
    clientOverrides: {
      authStatus: async () => ({ actor_kind: 'human', enrolled: false, enrollment_available: true }),
      registrationOptions: async () => ({ challenge_id: 'enrollment-1', public_key: {} }),
      register: async (...args) => { h.calls.push(['register', ...args]); return { enrolled: true }; },
    },
  });
  button(await h.start(), 'Register passkey').props.onClick();
  await flush();
  h.rt.unmount();
  assert.equal(signal.aborted, true);
  enrollment.resolve(proof);
  await flush();
  assert.deepEqual(h.calls, []);
});
