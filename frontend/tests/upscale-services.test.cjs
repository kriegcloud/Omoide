const assert = require('node:assert/strict');
const { test } = require('node:test');
const { runtime, load } = require('./datasetRuntime.cjs');
const repairs = load('services/repairs.ts', runtime(), {'../stores/mutationBus': {mutationBus: {emit(){}}}});

test('upscale submits provider parameters and per-run hosted consent for single and bulk', async () => {
  const requests = [];
  globalThis.fetch = async (url, init) => {
    requests.push({url, body: JSON.parse(init.body)});
    return new Response(JSON.stringify(url.endsWith('/bulk') ? [] : {id:'job',status:'queued'}));
  };
  const options = {provider:'hypir:ultra', params:{scale:4, texture_level:2}, hostedConsent:true};
  await repairs.startRepair(12, 'omoide-upscale-v1', options);
  await repairs.startBulkRepair([12,13], 'omoide-upscale-v1', options);
  for (const {body} of requests) {
    assert.equal(body.provider, 'hypir:ultra');
    assert.equal(body.hosted_consent, true);
    assert.deepEqual(body.params, {scale:4, texture_level:2});
  }
});
test('upscale registry and job reads use the shared repair service', async () => {
  const urls = [];
  globalThis.fetch = async url => { urls.push(url); return new Response(JSON.stringify({id:'x',status:'queued'})); };
  await repairs.listUpscalers();
  await repairs.getRepair('job/1');
  assert.deepEqual(urls, ['/api/repairs/upscalers','/api/repairs/job%2F1']);
});
