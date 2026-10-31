const assert = require('node:assert/strict');
const { test } = require('node:test');
const { runtime, load, nodes, text, button, deferred, flush } = require('./datasetRuntime.cjs');

const hash = 'a'.repeat(64);
const caption = { id: 'caption-1', text: 'A fixture still.', sha256: 'b'.repeat(64) };
const makeItem = (id = 'artifact-1', extra = {}) => ({ artifact_id: id, source_id: `source-${id}`, sha256: hash, width: 64, height: 64, generative_ancestry: false, caption, review: null, eligible: false, blockers: ['acceptance_required'], ...extra });
function dataset(extra = {}) {
  const items = extra.items ?? [makeItem()];
  return { id: 'fixture-1', name: 'Fixture one', revision: 1, policy_version: 'fixture-v1', actor: { id: 'reviewer', kind: 'fixture_human', human_presence_verified: false }, sources: items.map(item => ({ id: item.source_id, label: 'Geometric fixture', sha256: hash, status: 'ready' })), items, exports: [], remaining_count: items.length, blockers: ['acceptance_required'], ...extra };
}

function harness(initial = dataset(), options = {}) {
  const rt = runtime();
  let current = initial;
  let route = initial.id;
  const calls = [];
  const shortcuts = [];
  const service = load('services/curation.ts', rt);
  const passkeys = { ...load('services/curationPasskeys.ts', rt), ...options.passkeys };
  const client = {
    authStatus: async () => ({ actor_kind: current.actor.kind, enrolled: !!options.enrolled, enrollment_available: options.enrollmentAvailable ?? !options.enrolled }),
    list: async () => [{ id: current.id, name: current.name, revision: current.revision }],
    get: async () => current,
    review: async (id, input) => { calls.push(['review', id, input]); return current; },
    caption: async (...args) => { calls.push(['caption', ...args]); return current; },
    export: async (...args) => { calls.push(['export', ...args]); return { id: 'export-1', dataset_id: current.id, status: 'succeeded', snapshot_revision: current.revision, manifest_sha256: hash, item_count: 1, error_code: null }; },
    materialize: async (...args) => { calls.push(['materialize', ...args]); return current; },
    ...options.client,
  };
  const Page = load('pages/CurationReviewPage.tsx', rt, {
    '../services/curation': service,
    '../services/curationPasskeys': passkeys,
    '../config': { __esModule: true, default: { PRESENTATION_MODE: !!options.readOnly }, API: '' },
    '../components/DerivativeReviewPanel': { __esModule: true, default: 'DerivativeReviewPanel' },
    '../components/ConfirmDialog': { __esModule: true, default: 'ConfirmDialog' },
    '../hotkeys/useHotkey': { useHotkeys(bindings, handler, config) { shortcuts.push({ bindings, handler, config }); } },
    'react-router-dom': { useParams: () => ({ id: route }), useNavigate: () => () => {} },
  }).CurationWorkspace;
  const render = () => rt.render(() => Page({ client, mode: options.mode ?? 'fixture', onDisconnect() {} }));
  return { rt, client, calls, shortcuts, service, render, set(value) { current = value; }, route(value) { route = value; }, async start() { render(); await flush(); return render(); }, ready() { nodes(render(), node => node.type === 'DerivativeReviewPanel')[0].props.onReady(true); return render(); }, confirm() { nodes(render(), node => node.type === 'ConfirmDialog')[0].props.onConfirm(); } };
}

test('acceptance waits for verified and displayed comparison and sends only exact review inputs', async () => {
  const h = harness();
  let tree = await h.start();
  assert.equal(button(tree, 'Accept image and caption').props.disabled, true);
  button(tree, 'Accept image and caption').props.onClick();
  assert.equal(nodes(h.render(), n => n.type === 'ConfirmDialog')[0].props.open, false);
  tree = h.ready();
  assert.equal(button(tree, 'Accept image and caption').props.disabled, false);
  button(tree, 'Accept image and caption').props.onClick();
  h.confirm(); await flush();
  assert.deepEqual(h.calls[0], ['review', 'fixture-1', { artifact_id: 'artifact-1', caption_id: 'caption-1', asset_sha256: hash, caption_sha256: caption.sha256, decision: 'accept', rationale: '', expected_revision: 1 }]);
  h.rt.unmount();
});

test('an agent credential cannot accept even through an invoked disabled handler', async () => {
  const h = harness(dataset({ actor: { id: 'agent', kind: 'agent', human_presence_verified: false } }));
  await h.start(); const tree = h.ready();
  assert.equal(button(tree, 'Accept image and caption').props.disabled, true);
  button(tree, 'Accept image and caption').props.onClick();
  assert.equal(nodes(h.render(), n => n.type === 'ConfirmDialog')[0].props.open, false);
  assert.equal(nodes(tree, n => n.props?.label === 'Agent · proposals only').length, 1);
  assert.equal(button(tree, 'Reject').props.disabled, true);
  assert.equal(button(tree, 'Defer').props.disabled, true);
  assert.equal(h.calls.length, 0); h.rt.unmount();
});

test('generative ancestry always disables acceptance even with a fixture reviewer', async () => {
  const h = harness(dataset({ items: [makeItem('artifact-1', { generative_ancestry: true })] }));
  await h.start(); const tree = h.ready();
  assert.equal(button(tree, 'Accept image and caption').props.disabled, true);
  assert.equal(button(tree, 'Reject').props.disabled, true);
  assert.equal(button(tree, 'Defer').props.disabled, true);
  button(tree, 'Accept image and caption').props.onClick();
  assert.equal(nodes(h.render(), n => n.type === 'ConfirmDialog')[0].props.open, false);
  assert.equal(h.calls.length, 0); h.rt.unmount();
});

test('caption proposals clear server approval and block acceptance and navigation while unsaved', async () => {
  const accepted = makeItem('artifact-1', { eligible: true, review: { id: 'review-1', decision: 'accept', actor_kind: 'fixture_human' } });
  const h = harness(dataset({ items: [accepted, makeItem('artifact-2')], remaining_count: 1 }));
  await h.start(); let tree = h.ready();
  nodes(tree, n => n.props?.label === 'Caption proposal')[0].props.onChange({ target: { value: 'A revised caption.' } });
  tree = h.render();
  assert.equal(button(tree, 'Accept image and caption').props.disabled, true);
  assert.equal(button(tree, 'Next').props.disabled, true);
  assert.equal(button(tree, 'Export accepted stills').props.disabled, true);
  h.set(dataset({ revision: 2, items: [makeItem('artifact-1', { caption: { ...caption, id: 'caption-2', text: 'A revised caption.', sha256: 'c'.repeat(64) } }), makeItem('artifact-2')] }));
  button(tree, 'Save caption proposal').props.onClick(); await flush(); tree = h.render();
  assert.deepEqual(h.calls[0], ['caption', 'fixture-1', 'artifact-1', 'A revised caption.', 1]);
  assert.ok(text(tree).includes('Any prior approval is cleared'));
  assert.equal(nodes(tree, n => n.props?.label === 'Accepted · export eligible').length, 0);
  assert.ok(text(tree).includes('2 remaining to review')); h.rt.unmount();
});

test('stale caption save preserves text, requires explicit refresh, and uses the new revision', async () => {
  const h = harness();
  h.client.caption = async () => { throw new h.service.CurationError(409, 'revision_conflict'); };
  let tree = await h.start();
  nodes(tree, n => n.props?.label === 'Caption proposal')[0].props.onChange({ target: { value: 'My unsaved caption.' } });
  button(h.render(), 'Save caption proposal').props.onClick(); await flush(); tree = h.render();
  assert.ok(text(tree).includes('Your decision was not applied'));
  assert.equal(button(tree, 'Save caption proposal').props.disabled, true);
  h.set(dataset({ revision: 7, items: [makeItem('artifact-1', { caption: { ...caption, id: 'caption-7', text: 'Another reviewer caption.' } })] }));
  button(tree, 'Refresh review').props.onClick(); await flush(); tree = h.render();
  assert.equal(nodes(tree, n => n.props?.label === 'Caption proposal')[0].props.value, 'My unsaved caption.');
  assert.ok(text(tree).includes('Another reviewer caption.'));
  h.client.caption = async (...args) => { h.calls.push(args); return dataset({ revision: 8 }); };
  button(tree, 'Save caption proposal').props.onClick(); await flush();
  assert.equal(h.calls[0][3], 7); h.rt.unmount();
});

test('reject and defer are distinct persisted decisions behind confirmation', async () => {
  for (const [label, decision] of [['Reject', 'reject'], ['Defer', 'defer']]) {
    const h = harness(); let tree = await h.start();
    button(tree, label).props.onClick();
    assert.equal(h.calls.length, 0);
    h.confirm(); await flush();
    assert.equal(h.calls[0][2].decision, decision); h.rt.unmount();
  }
});

test('prepare retries use a new idempotency key when the requested revision changes', async () => {
  const initial = dataset({ items: [], sources: [{ id: 'source-1', label: 'Fixture', sha256: hash, status: 'registered' }], blockers: ['unmaterialized_sources'] });
  const h = harness(initial);
  h.client.materialize = async (...args) => { h.calls.push(args); throw new h.service.CurationError(409, 'revision_conflict'); };
  let tree = await h.start(); button(tree, 'Prepare still').props.onClick(); await flush();
  h.set({ ...initial, revision: 2 });
  button(h.render(), 'Refresh review').props.onClick(); await flush();
  button(h.render(), 'Prepare still').props.onClick(); await flush();
  assert.equal(h.calls.length, 2);
  assert.equal(h.calls[0][2], 1); assert.equal(h.calls[1][2], 2);
  assert.notEqual(h.calls[0][3], h.calls[1][3]); h.rt.unmount();
});

test('refreshing an unchanged revision still remounts the comparison for new verification', async () => {
  const h = harness(); await h.start(); let tree = h.ready();
  const firstKey = nodes(tree, n => n.type === 'DerivativeReviewPanel')[0].key;
  button(tree, 'Refresh review').props.onClick(); await flush(); tree = h.render();
  assert.notEqual(nodes(tree, n => n.type === 'DerivativeReviewPanel')[0].key, firstKey);
  assert.equal(button(tree, 'Accept image and caption').props.disabled, true); h.rt.unmount();
});

test('duplicate mutation clicks and navigation while pending cannot retarget a review', async () => {
  const waiting = deferred();
  const h = harness(dataset({ items: [makeItem(), makeItem('artifact-2')] }), { client: { review: async (...args) => { h.calls.push(args); return waiting.promise; } } });
  await h.start(); let tree = h.ready();
  button(tree, 'Accept image and caption').props.onClick(); h.confirm(); h.confirm();
  tree = h.render(); button(tree, 'Next').props.onClick();
  assert.equal(h.calls.length, 1);
  assert.equal(nodes(h.render(), n => n.type === 'DerivativeReviewPanel')[0].props.item.artifact_id, 'artifact-1');
  waiting.resolve(dataset({ revision: 2 })); await flush(); h.rt.unmount();
});

test('a late mutation response cannot replace a different route', async () => {
  const waiting = deferred(); const h = harness();
  h.client.review = () => waiting.promise;
  await h.start(); button(h.ready(), 'Accept image and caption').props.onClick(); h.confirm();
  h.set(dataset({ id: 'fixture-2', name: 'Fixture two', items: [makeItem('artifact-2')] }));
  h.route('fixture-2'); h.render(); await flush();
  waiting.resolve(dataset({ name: 'Old fixture response' })); await flush();
  const tree = h.render(); assert.ok(text(tree).includes('Fixture two')); assert.ok(!text(tree).includes('Old fixture response')); h.rt.unmount();
});

test('export is blocked until the server reports current accepted pairs and no blockers', async () => {
  const h = harness(); let tree = await h.start();
  assert.equal(button(tree, 'Export accepted stills').props.disabled, true);
  button(tree, 'Export accepted stills').props.onClick(); assert.equal(h.calls.length, 0);
  h.set(dataset({ revision: 3, remaining_count: 0, blockers: [], items: [makeItem('artifact-1', { eligible: true, review: { id: 'review-3', decision: 'accept', actor_kind: 'fixture_human' } })] }));
  button(tree, 'Refresh review').props.onClick(); await flush(); tree = h.render();
  assert.equal(button(tree, 'Export accepted stills').props.disabled, false);
  button(tree, 'Export accepted stills').props.onClick(); await flush(); tree = h.render();
  assert.equal(h.calls[0][0], 'export'); assert.equal(h.calls[0][2], 3);
  assert.ok(text(tree).includes('Fixture export verified')); h.rt.unmount();
});

test('read-only mode disables all mutations independently of fixture authority', async () => {
  const h = harness(dataset({ blockers: [], items: [makeItem('artifact-1', { eligible: true })] }), { readOnly: true });
  await h.start(); const tree = h.ready();
  for (const label of ['Accept image and caption', 'Reject', 'Defer', 'Save caption proposal', 'Export accepted stills']) assert.equal(button(tree, label).props.disabled, true);
  h.rt.unmount();
});

test('review shortcuts are registered through the shared typing and dialog guards', async () => {
  const h = harness(); await h.start(); h.ready();
  const accept = h.shortcuts.findLast(value => value.bindings.some(binding => binding.key === 'a'));
  assert.equal(accept.config.scope, 'page'); assert.equal(accept.config.enabled, true); assert.equal(accept.config.allowInInput, undefined);
  const reject = h.shortcuts.findLast(value => value.bindings.some(binding => binding.key === 'x'));
  assert.equal(reject.config.destructive, true); h.rt.unmount();
});

test('disabled status exposes no credential entry or workspace', async () => {
  const rt = runtime(); const Page = load('pages/CurationReviewPage.tsx', rt, { '../services/curation': { getCurationStatus: async () => ({ enabled: false, fixture_only: true, generative_enabled: false, human_presence_verified: false }) } }).default;
  rt.render(Page); await flush(); const tree = rt.render(Page);
  assert.ok(text(tree).includes('Still review is disabled'));
  assert.equal(nodes(tree, n => n.props?.label === 'Fixture access credential').length, 0); rt.unmount();
});

test('image service rejects changed bytes and authenticates without credential-bearing URLs', async () => {
  const original = global.fetch; const calls = [];
  try {
    const service = load('services/curation.ts', runtime());
    const bytes = new TextEncoder().encode('fixture bytes');
    const digest = Buffer.from(await crypto.subtle.digest('SHA-256', bytes)).toString('hex');
    global.fetch = async (url, init) => { calls.push([url, init]); return new Response(bytes, { headers: { 'Content-Type': 'image/png' } }); };
    const client = service.createCurationClient('test-credential');
    const blob = await client.image('artifacts', 'artifact-1', digest);
    assert.equal(await blob.text(), 'fixture bytes');
    assert.equal(calls[0][0], '/api/curation/artifacts/artifact-1/content');
    assert.equal(calls[0][1].headers.Authorization, 'Bearer test-credential');
    assert.equal(calls[0][1].cache, 'no-store');
    await assert.rejects(client.image('artifacts', 'artifact-1', hash), /could not be verified/);
  } finally { global.fetch = original; }
});

test('server error details cannot leak locators or credentials through UI messages', async () => {
  const original = global.fetch;
  try {
    const service = load('services/curation.ts', runtime());
    global.fetch = async () => new Response(JSON.stringify({ detail: { code: 'forbidden', path: '/private/source', credential: 'not-for-display' } }), { status: 403 });
    await assert.rejects(service.createCurationClient('test').get('fixture'), error => error instanceof service.CurationError && !error.message.includes('/private') && !error.message.includes('not-for-display'));
  } finally { global.fetch = original; }
});

test('comparison only reports readiness after both exact images display and revokes URLs on unmount', async () => {
  const rt = runtime(); const calls = []; const ready = [];
  const client = { image: async (...args) => { calls.push(args); return new Blob(['fixture'], { type: 'image/png' }); } };
  const Panel = load('components/DerivativeReviewPanel.tsx', rt).default;
  const item = makeItem(); const source = dataset().sources[0]; const onReady = value => ready.push(value);
  const render = () => rt.render(() => Panel({ client, item, source, onReady }));
  render(); await flush(); let tree = render();
  assert.deepEqual(calls.map(value => value.slice(0, 3)), [['sources', source.id, hash], ['artifacts', item.artifact_id, hash]]);
  const images = nodes(tree, n => n.props?.component === 'img');
  images[0].props.onLoad(); render(); assert.equal(ready.at(-1), false);
  images[1].props.onLoad(); render(); assert.equal(ready.at(-1), true);
  const url = images[0].props.src; rt.unmount();
  await assert.rejects(fetch(url));
});

const productionActor = { id: 'human-1', kind: 'human', human_presence_verified: false, operations: ['read', 'preview', 'review', 'caption', 'materialize', 'export', 'enroll'] };
const productionDataset = (extra = {}) => dataset({ actor: productionActor, policy_version: 'production-v1', ...extra });
const proof = { id: 'passkey-id', rawId: 'aWQ', type: 'public-key', clientExtensionResults: {}, response: { clientDataJSON: 'Y2xpZW50', authenticatorData: 'YXV0aA', signature: 'c2ln', userHandle: null } };
const dialog = h => nodes(h.render(), node => node.type === 'ConfirmDialog')[0];

for (const [label, decision] of [['Accept image and caption', 'accept'], ['Reject', 'reject'], ['Defer', 'defer']]) {
  test(`production ${decision} requires exact comparison, explicit confirmation and fresh passkey proof`, async () => {
    const order = []; const assertion = deferred(); const submitted = deferred();
    const options = { challenge: 'Y2hhbGxlbmdl', userVerification: 'required', rpId: 'localhost' };
    const h = harness(productionDataset(), { mode: 'production', enrolled: true,
      passkeys: { confirmCurationPasskey: async value => { order.push(['assert', value]); return assertion.promise; } },
      client: {
        reviewOptions: async (...args) => { order.push(['options', ...args]); return { challenge_id: `challenge-${decision}`, public_key: options }; },
        review: async (...args) => { order.push(['review', ...args]); return submitted.promise; },
      },
    });
    let tree = await h.start();
    assert.equal(button(tree, label).props.disabled, true);
    button(tree, label).props.onClick(); assert.equal(dialog(h).props.open, false);
    tree = h.ready(); button(tree, label).props.onClick();
    assert.equal(order.length, 0); assert.equal(dialog(h).props.confirmLabel, 'Confirm with passkey');
    h.confirm(); h.confirm(); await flush();
    assert.deepEqual(order.map(call => call[0]), ['options', 'assert']);
    assert.equal(dialog(h).props.loading, true);
    assert.ok(!text(h.render()).includes('This recorded decision was verified'));
    assertion.resolve(proof); await flush();
    assert.deepEqual(order.map(call => call[0]), ['options', 'assert', 'review']);
    assert.deepEqual(order[0][2], { artifact_id: 'artifact-1', caption_id: 'caption-1', asset_sha256: hash, caption_sha256: caption.sha256, decision, rationale: '', expected_revision: 1 });
    assert.deepEqual(order[2][2], { ...order[0][2], presence: { challenge_id: `challenge-${decision}`, credential: proof } });
    assert.ok(!text(h.render()).includes('This recorded decision was verified'));
    submitted.resolve(productionDataset({ revision: 2, items: [makeItem('artifact-1', { review: { id: 'review-verified', decision, actor_kind: 'human', human_presence_verified: true, presence_evidence: { method: 'webauthn', user_present: true, user_verified: true } } })] }));
    await flush();
    assert.equal(dialog(h).props.open, false);
    assert.ok(text(h.render()).includes('This recorded decision was verified with the reviewer'));
    h.rt.unmount();
  });
}

test('production enrollment unlocks a ceremony only after server verification and never records a review', async () => {
  const verify = deferred(); const order = [];
  const h = harness(productionDataset(), { mode: 'production',
    passkeys: { registerCurationPasskey: async () => { order.push('create'); return proof; } },
    client: {
      registrationOptions: async () => { order.push('options'); return { challenge_id: 'enroll-1', public_key: {} }; },
      register: async input => { order.push(['verify', input]); return verify.promise; },
    },
  });
  await h.start(); let tree = h.ready();
  assert.equal(button(tree, 'Accept image and caption').props.disabled, true);
  assert.ok(button(tree, 'Register passkey'));
  button(tree, 'Register passkey').props.onClick(); await flush();
  assert.deepEqual(order, ['options', 'create', ['verify', { challenge_id: 'enroll-1', credential: proof }]]);
  assert.equal(button(h.render(), 'Accept image and caption').props.disabled, true);
  verify.resolve({ enrolled: true }); await flush(); tree = h.render();
  assert.equal(button(tree, 'Accept image and caption').props.disabled, false);
  assert.equal(button(tree, 'Register passkey'), undefined);
  assert.equal(h.calls.length, 0); assert.equal(dialog(h).props.open, false);
  assert.ok(text(tree).includes('No review decision has been recorded'));
  assert.ok(!text(tree).includes('This recorded decision was verified'));
  h.rt.unmount();
});

test('enrollment cancellation keeps an unsaved caption and cannot enable decisions', async () => {
  const passkeys = load('services/curationPasskeys.ts', runtime());
  const h = harness(productionDataset(), { mode: 'production',
    passkeys: { ...passkeys, registerCurationPasskey: async () => { throw new passkeys.CurationPasskeyError(true, 'Cancelled; no review was submitted.'); } },
    client: { registrationOptions: async () => ({ challenge_id: 'enroll-1', public_key: {} }) },
  });
  let tree = await h.start();
  nodes(tree, n => n.props?.label === 'Caption proposal')[0].props.onChange({ target: { value: 'Keep my draft.' } });
  button(h.render(), 'Register passkey').props.onClick(); await flush(); tree = h.render();
  assert.equal(nodes(tree, n => n.props?.label === 'Caption proposal')[0].props.value, 'Keep my draft.');
  assert.equal(button(tree, 'Accept image and caption').props.disabled, true);
  assert.equal(button(tree, 'Save caption proposal').props.disabled, false);
  assert.equal(h.calls.length, 0); h.rt.unmount();
});

test('cancelled review preserves the confirmed decision and note without submitting or automatically retrying', async () => {
  const passkeys = load('services/curationPasskeys.ts', runtime()); const order = [];
  const h = harness(productionDataset(), { mode: 'production', enrolled: true,
    passkeys: { ...passkeys, confirmCurationPasskey: async () => { order.push('assert'); throw new passkeys.CurationPasskeyError(true, 'Passkey cancelled. Your draft and decision are kept.'); } },
    client: { reviewOptions: async (...args) => { order.push(['options', ...args]); return { challenge_id: `fresh-${order.length}`, public_key: {} }; } },
  });
  await h.start(); let tree = h.ready();
  nodes(tree, n => n.props?.label === 'Review note (optional)')[0].props.onChange({ target: { value: 'Keep this exact review note.' } });
  button(h.render(), 'Defer').props.onClick(); h.confirm(); await flush(); tree = h.render();
  assert.equal(h.calls.length, 0); assert.equal(order.length, 2);
  assert.equal(dialog(h).props.open, true); assert.equal(dialog(h).props.loading, false);
  assert.ok(text(dialog(h).props.message).includes('Your draft and decision are kept'));
  assert.equal(nodes(tree, n => n.props?.label === 'Review note (optional)')[0].props.value, 'Keep this exact review note.');
  assert.equal(nodes(tree, n => n.props?.label === 'Caption proposal')[0].props.value, caption.text);
  await flush(); assert.equal(order.length, 2);
  h.confirm(); await flush();
  assert.equal(order.length, 4); assert.deepEqual(order[0][2], order[2][2]);
  assert.equal(h.calls.length, 0); h.rt.unmount();
});

test('route changes cancel a pending ceremony and never submit its late assertion', async () => {
  const waiting = deferred(); let assertionSignal; let optionsCount = 0;
  const h = harness(productionDataset(), { mode: 'production', enrolled: true,
    passkeys: { confirmCurationPasskey: (_, signal) => { assertionSignal = signal; return waiting.promise; } },
    client: { reviewOptions: async () => { optionsCount++; return { challenge_id: 'old-ceremony', public_key: {} }; } },
  });
  await h.start(); button(h.ready(), 'Accept image and caption').props.onClick(); h.confirm(); await flush();
  h.set(productionDataset({ id: 'new-dataset', name: 'New dataset' })); h.route('new-dataset'); h.render(); await flush();
  assert.equal(assertionSignal.aborted, true);
  waiting.resolve(proof); await flush();
  assert.equal(h.calls.length, 0); assert.equal(optionsCount, 1);
  assert.ok(text(h.render()).includes('New dataset')); h.rt.unmount();
});

test('leaving during challenge issuance never opens a late passkey prompt', async () => {
  const waiting = deferred(); let promptCount = 0;
  const h = harness(productionDataset(), { mode: 'production', enrolled: true,
    passkeys: { confirmCurationPasskey: async () => { promptCount++; return proof; } },
    client: { reviewOptions: () => waiting.promise },
  });
  await h.start(); button(h.ready(), 'Reject').props.onClick(); h.confirm(); h.rt.unmount();
  waiting.resolve({ challenge_id: 'late', public_key: {} }); await flush();
  assert.equal(promptCount, 0); assert.equal(h.calls.length, 0);
});

test('a denied review proof requires refresh and cannot reuse the proof or claim a verified decision', async () => {
  let count = 0;
  const h = harness(productionDataset(), { mode: 'production', enrolled: true,
    passkeys: { confirmCurationPasskey: async () => proof },
    client: { reviewOptions: async () => ({ challenge_id: 'stale', public_key: {} }) },
  });
  h.client.review = async () => { count++; throw new h.service.CurationError(409, 'review_hash_conflict'); };
  await h.start(); button(h.ready(), 'Accept image and caption').props.onClick(); h.confirm(); await flush();
  const tree = h.render();
  assert.equal(count, 1); assert.equal(dialog(h).props.open, false);
  assert.equal(button(tree, 'Accept image and caption').props.disabled, true);
  assert.ok(text(tree).includes('Refresh the review before continuing'));
  assert.ok(!text(tree).includes('This recorded decision was verified'));
  h.confirm(); await flush(); assert.equal(count, 1); h.rt.unmount();
});

for (const kind of ['agent', 'fixture_human']) {
  test(`${kind} cannot invoke production registration, decisions, or passkey assertions`, async () => {
    const h = harness(productionDataset({ actor: { ...productionActor, kind } }), { mode: 'production', enrolled: true });
    await h.start(); const tree = h.ready();
    assert.equal(button(tree, 'Register passkey'), undefined);
    for (const label of ['Accept image and caption', 'Reject', 'Defer']) {
      assert.equal(button(tree, label).props.disabled, true); button(tree, label).props.onClick();
    }
    assert.equal(dialog(h).props.open, false); assert.equal(h.calls.length, 0); h.rt.unmount();
  });
}

test('metadata-only production agents cannot request image previews or mutate captions', async () => {
  const h = harness(productionDataset({ actor: { ...productionActor, kind: 'agent', operations: ['read'] } }), { mode: 'production' });
  const tree = await h.start();
  assert.equal(nodes(tree, node => node.type === 'DerivativeReviewPanel').length, 0);
  assert.ok(text(tree).includes('Metadata access does not permit viewing images'));
  assert.equal(nodes(tree, n => n.props?.label === 'Caption proposal')[0].props.disabled, true);
  button(tree, 'Save caption proposal').props.onClick();
  assert.equal(h.calls.length, 0); h.rt.unmount();
});

test('server bearer presence markers and enrollment do not become a verified review badge', async () => {
  const h = harness(productionDataset({ actor: { ...productionActor, human_presence_verified: true }, items: [makeItem('artifact-1', { review: { id: 'fixture-old', decision: 'accept', actor_kind: 'fixture_human', human_presence_verified: true, presence_evidence: { method: 'webauthn', user_present: true, user_verified: true } } })] }), { mode: 'production', enrolled: true });
  const tree = await h.start();
  assert.ok(text(tree).includes('Passkey registered'));
  assert.ok(!text(tree).includes('This recorded decision was verified'));
  h.rt.unmount();
});

test('production read-only mode disables enrollment and all decision ceremonies', async () => {
  const h = harness(productionDataset(), { mode: 'production', readOnly: true });
  await h.start(); const tree = h.ready();
  assert.equal(button(tree, 'Register passkey').props.disabled, true);
  button(tree, 'Register passkey').props.onClick();
  for (const label of ['Accept image and caption', 'Reject', 'Defer', 'Save caption proposal', 'Export accepted stills']) assert.equal(button(tree, label).props.disabled, true);
  assert.equal(h.calls.length, 0); h.rt.unmount();
});

test('production availability uses its own connection copy and legacy review notice', async () => {
  const rt = runtime(); const Page = load('pages/CurationReviewPage.tsx', rt, { '../services/curation': { getCurationStatus: async () => ({ enabled: true, mode: 'production', fixture_only: false, generative_enabled: false }) } }).default;
  rt.render(Page); await flush(); const tree = rt.render(Page);
  assert.ok(text(tree).includes('Production still review'));
  assert.ok(text(tree).includes('Generative preparation and acceptance are disabled'));
  assert.ok(text(tree).includes('Legacy review and approval actions are unavailable'));
  assert.equal(nodes(tree, n => n.props?.label === 'Review access credential').length, 1);
  assert.equal(nodes(tree, n => n.props?.label === 'Fixture access credential').length, 0);
  rt.unmount();
});

test('entry link follows explicit server mode and never treats an unknown mode as fixture authority', async () => {
  for (const mode of ['fixture', 'production', 'disabled', undefined]) {
    const rt = runtime(); const Link = load('components/CurationEntryLink.tsx', rt, { '../services/curation': { getCurationStatus: async () => ({ enabled: true, mode, fixture_only: true }) } }).default;
    rt.render(Link); await flush(); const tree = rt.render(Link);
    assert.equal(text(tree), mode === 'fixture' ? 'Still review (fixtures)' : mode === 'production' ? 'Still review' : ''); rt.unmount();
  }
});

test('passkey API requests keep authorization out of URLs and bind proof to the submitted review fields', async () => {
  const original = global.fetch; const calls = [];
  try {
    const service = load('services/curation.ts', runtime());
    global.fetch = async (url, init) => { calls.push([url, init]); return new Response(JSON.stringify({ enrolled: true })); };
    const client = service.createCurationClient('memory-only'); const input = { artifact_id: 'exact', caption_id: 'caption', asset_sha256: hash, caption_sha256: caption.sha256, decision: 'defer', rationale: 'Note.', expected_revision: 9 };
    await client.authStatus(); await client.registrationOptions(); await client.register({ challenge_id: 'reg', credential: proof });
    await client.reviewOptions('dataset/1', input); await client.review('dataset/1', { ...input, presence: { challenge_id: 'decision', credential: proof } });
    assert.deepEqual(calls.map(([url]) => url), ['/api/curation/auth/status', '/api/curation/auth/registration/options', '/api/curation/auth/registration/verify', '/api/curation/datasets/dataset%2F1/review-options', '/api/curation/datasets/dataset%2F1/reviews']);
    for (const [url, init] of calls) { assert.ok(!url.includes('memory-only')); assert.equal(init.headers.Authorization, 'Bearer memory-only'); assert.equal(init.cache, 'no-store'); }
    assert.deepEqual(JSON.parse(calls[3][1].body), input);
    assert.deepEqual(JSON.parse(calls[4][1].body), { ...input, presence: { challenge_id: 'decision', credential: proof } });
  } finally { global.fetch = original; }
});

test('revoked enrollment and missing enroll permission require operator recovery without re-enrollment', async () => {
  for (const options of [{ enrollmentAvailable: false }, { actor: { ...productionActor, operations: ['read', 'preview', 'review'] } }]) {
    const h = harness(productionDataset(options.actor ? { actor: options.actor } : {}), { mode: 'production', ...options });
    await h.start(); const tree = h.ready();
    assert.equal(button(tree, 'Register passkey'), undefined);
    assert.equal(button(tree, 'Accept image and caption').props.disabled, true);
    assert.ok(text(tree).includes('Passkey setup requires the local operator')); assert.equal(h.calls.length, 0); h.rt.unmount();
  }
});
