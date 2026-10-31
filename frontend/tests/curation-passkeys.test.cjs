const assert = require('node:assert/strict');
const { test } = require('node:test');
const { load, runtime } = require('./datasetRuntime.cjs');
const passkeys = load('services/curationPasskeys.ts', runtime());
const bytes = (...values) => new Uint8Array(values).buffer;
const challenge = 'APv_';
const creation = { challenge, rp: { id: 'localhost', name: 'Still review' }, user: { id: 'AAEC', name: 'reviewer', displayName: 'Reviewer' }, pubKeyCredParams: [{ type: 'public-key', alg: -7 }], authenticatorSelection: { userVerification: 'required', residentKey: 'preferred' }, excludeCredentials: [{ id: '-_8', type: 'public-key', transports: ['usb'] }], attestation: 'none', timeout: 60000 };
const assertion = { challenge, rpId: 'localhost', userVerification: 'required', allowCredentials: [{ id: '-_8', type: 'public-key', transports: ['usb'] }], timeout: 60000 };
const credential = response => ({ id: '-_8', rawId: bytes(251, 255), type: 'public-key', authenticatorAttachment: 'cross-platform', response: { clientDataJSON: bytes(0, 1, 2), ...response }, getClientExtensionResults: () => ({ credProps: { rk: true }, extensionBytes: bytes(251, 255) }) });

function browser(credentials, secure = true) {
  const navigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  const context = Object.getOwnPropertyDescriptor(globalThis, 'isSecureContext');
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { credentials } });
  Object.defineProperty(globalThis, 'isSecureContext', { configurable: true, value: secure });
  return () => {
    if (navigator) Object.defineProperty(globalThis, 'navigator', navigator); else delete globalThis.navigator;
    if (context) Object.defineProperty(globalThis, 'isSecureContext', context); else delete globalThis.isSecureContext;
  };
}

test('registration converts challenge, user ID and exclusions without mutating server-owned RP/options', () => {
  const original = JSON.stringify(creation); const parsed = passkeys.registrationOptions(creation);
  assert.deepEqual(new Uint8Array(parsed.challenge), new Uint8Array([0, 251, 255]));
  assert.deepEqual(new Uint8Array(parsed.user.id), new Uint8Array([0, 1, 2]));
  assert.deepEqual(new Uint8Array(parsed.excludeCredentials[0].id), new Uint8Array([251, 255]));
  assert.equal(parsed.rp.id, 'localhost'); assert.deepEqual(parsed.excludeCredentials[0].transports, ['usb']);
  assert.equal(parsed.authenticatorSelection.userVerification, 'required');
  assert.equal(JSON.stringify(creation), original);
});

test('assertion converts allowed IDs and preserves exact server RP, challenge, UV and timeout', () => {
  const original = JSON.stringify(assertion); const parsed = passkeys.assertionOptions(assertion);
  assert.deepEqual(new Uint8Array(parsed.challenge), new Uint8Array([0, 251, 255]));
  assert.deepEqual(new Uint8Array(parsed.allowCredentials[0].id), new Uint8Array([251, 255]));
  assert.equal(parsed.rpId, 'localhost'); assert.equal(parsed.userVerification, 'required'); assert.equal(parsed.timeout, 60000);
  assert.equal(JSON.stringify(assertion), original);
});

test('malformed or noncanonical base64url and verification downgrades fail closed', () => {
  for (const value of ['', 'a', 'AB', 'a+b/', 'AA==', 'Zm9v\n']) assert.throws(() => passkeys.decodePasskeyBytes(value), passkeys.CurationPasskeyError);
  for (const userVerification of [undefined, 'discouraged', 'preferred']) {
    assert.throws(() => passkeys.assertionOptions({ ...assertion, userVerification }), /require user verification/);
    assert.throws(() => passkeys.registrationOptions({ ...creation, authenticatorSelection: { userVerification } }), /require user verification/);
  }
});

test('base64url serialization preserves every binary byte and strips padding', () => {
  const all = Uint8Array.from({ length: 256 }, (_, index) => index);
  const encoded = passkeys.encodePasskeyBytes(all.buffer);
  assert.equal(encoded, Buffer.from(all).toString('base64url'));
  assert.deepEqual(new Uint8Array(passkeys.decodePasskeyBytes(encoded)), all);
});

test('registration serializes browser attestation and transport evidence without accepting it as review proof', () => {
  const json = passkeys.serializePasskey(credential({ attestationObject: bytes(251, 255, 0), getTransports: () => ['usb', 'internal'] }), 'registration');
  assert.deepEqual(json, { id: '-_8', rawId: '-_8', type: 'public-key', authenticatorAttachment: 'cross-platform', clientExtensionResults: { credProps: { rk: true }, extensionBytes: '-_8' }, response: { clientDataJSON: 'AAEC', attestationObject: '-_8A', transports: ['usb', 'internal'] } });
  assert.throws(() => passkeys.serializePasskey(credential({ attestationObject: bytes(1) }), 'assertion'), /did not return a passkey confirmation/);
});

test('assertion serializes authenticator data, signature, user handle and client data distinctly', () => {
  for (const handle of [null, bytes(7, 8)]) {
    const json = passkeys.serializePasskey(credential({ authenticatorData: bytes(3, 4), signature: bytes(5, 6), userHandle: handle }), 'assertion');
    assert.deepEqual(json.response, { clientDataJSON: 'AAEC', authenticatorData: 'AwQ', signature: 'BQY', userHandle: handle ? 'Bwg' : null });
    assert.equal(json.response.attestationObject, undefined);
  }
});

test('native create and get receive decoded options and the cancellation signal', async () => {
  const calls = []; const controller = new AbortController();
  const restore = browser({
    create: async options => { calls.push(['create', options]); return credential({ attestationObject: bytes(1), getTransports: () => ['usb'] }); },
    get: async options => { calls.push(['get', options]); return credential({ authenticatorData: bytes(2), signature: bytes(3), userHandle: null }); },
  });
  try {
    const registered = await passkeys.registerCurationPasskey(creation, controller.signal);
    const confirmed = await passkeys.confirmCurationPasskey(assertion, controller.signal);
    assert.equal(registered.response.attestationObject, 'AQ'); assert.equal(confirmed.response.signature, 'Aw');
    for (const [, options] of calls) { assert.equal(options.signal, controller.signal); assert.ok(options.publicKey.challenge instanceof ArrayBuffer); }
    assert.deepEqual(calls.map(([method]) => method), ['create', 'get']);
  } finally { restore(); }
});

for (const error of [null, new DOMException('private authenticator detail', 'NotAllowedError'), new DOMException('private authenticator detail', 'AbortError')]) {
  test(`native ${error?.name ?? 'null response'} cancellation is safe and never retries`, async () => {
    let count = 0; const restore = browser({ get: async () => { count++; if (error) throw error; return null; } });
    try {
      await assert.rejects(passkeys.confirmCurationPasskey(assertion), reason => reason instanceof passkeys.CurationPasskeyError && reason.cancelled && !reason.message.includes('private authenticator detail'));
      assert.equal(count, 1);
    } finally { restore(); }
  });
}

test('unsupported or insecure browser fails before invoking a credential API', async () => {
  let count = 0; const restore = browser({ get: async () => { count++; } }, false);
  try { await assert.rejects(passkeys.confirmCurationPasskey(assertion), /secure connection/); assert.equal(count, 0); }
  finally { restore(); }
});

test('browser security failures have a safe actionable message and do not expose raw exception details', async () => {
  const restore = browser({ get: async () => { throw new DOMException('sensitive RP diagnostic and key name', 'SecurityError'); } });
  try {
    await assert.rejects(passkeys.confirmCurationPasskey(assertion), reason => reason instanceof passkeys.CurationPasskeyError && !reason.cancelled && reason.message.includes('site address') && !reason.message.includes('sensitive'));
  } finally { restore(); }
});
