const { test } = require('node:test');
const assert = require('node:assert/strict');
const runtime = require('./peopleFaceRuntime.cjs');
for (const [family, method, args, url] of [
  ['faces', 'assignFace', [[1], 2, 'manual', '', '11111111-1111-4111-8111-111111111111'], '/api/faces/assign'],
  ['people', 'hidePersonsBulk', [[2], '11111111-1111-4111-8111-111111111111'], '/api/person/bulk-hide'],
]) test(`${family} sends operation identity and exposes durable receipt`, async () => {
  const api = runtime()[family];
  const receipt = { operation_id: args.at(-1), state: 'partial', applied: [], skipped: [{key: family === 'faces' ? 'face:1' : 'person:2', reason:'missing'}], failed: [], unknown: [] };
  const original = global.fetch;
  global.fetch = async (path, init) => {
    assert.equal(path, url);
    assert.equal(JSON.parse(init.body).operation_id, receipt.operation_id);
    return { ok: true, json: async () => ({receipt, hidden_ids: [], skipped_ids: [2]}) };
  };
  try { assert.deepEqual((await api[method](...args)).receipt, receipt); }
  finally { global.fetch = original; }
});
for (const family of ['faces', 'people']) test(`${family} reconciles lost response by lookup without replay`, async () => {
  const { core, faces, people } = runtime();
  const original = global.fetch;
  let writes = 0, lookups = 0, operationId;
  global.fetch = async (url, options) => {
    if (options?.method) {
      writes++;
      operationId = JSON.parse(options.body).operation_id;
      throw new TypeError('response lost after commit');
    }
    lookups++;
    assert.equal(url, `/api/${family === 'faces' ? 'faces' : 'person'}/operations/${operationId}`);
    return { ok: true, json: async () => ({ operation_id: operationId, state: 'partial', applied: [family === 'faces' ? 'face:1' : 'person:1'], skipped: [{key: family === 'faces' ? 'face:2' : 'person:2', reason: 'missing'}], failed: [], unknown: [], previous_ownership: {'face:1': 7} }) };
  };
  try {
    const handle = family === 'faces'
      ? faces.startFaceMutation('Assign', [1, 2], id => faces.assignFace([1, 2], 9, 'manual', '', id), 'faces')
      : people.startPeopleMutation('Hide', [1, 2], id => people.hidePersonsBulk([1, 2], id));
    await handle.done;
    assert.equal(core.hasOperationLookup(handle.id), true);
    const reconciled = await core.reconcileOperation(handle.id);
    assert.equal(reconciled.state, 'partially_committed');
    assert.deepEqual(reconciled.receipt.previous_ownership, {'face:1': 7});
    assert.equal(writes, 1);
    assert.ok(lookups >= 1);
  } finally { global.fetch = original; }
});
test('face Undo restores only acknowledged faces to recorded previous owners', async () => {
 const {faces} = runtime(); const original = global.fetch, calls = [];
 global.fetch = async (url, options) => {
  const body = JSON.parse(options.body); calls.push({url, body});
  return {ok:true,json:async()=>({receipt:{operation_id:body.operation_id,state:'succeeded',applied:body.face_ids.map(id=>`face:${id}`),skipped:[],failed:[],unknown:[]}})};
 };
 try {
  await faces.restoreFaceOwnership({operation_id:crypto.randomUUID(),state:'partial',applied:['face:1','face:2'],skipped:[{key:'face:3',reason:'missing'}],failed:[],unknown:[],previous_ownership:{'face:1':null,'face:2':7,'face:3':8}});
  assert.deepEqual(calls.map(c=>[c.url,c.body.face_ids,c.body.person_id,c.body.source]), [['/api/faces/detach',[1],undefined,undefined],['/api/faces/assign',[2],7,'undo']]);
 } finally {global.fetch=original;}
});
test('media favorite carries controller identity and reconciles through media lookup', async () => {
 const {core,media} = runtime(); const original = global.fetch; let operationId,writes=0;
 global.fetch = async (url, options) => {
  if (options) {writes++; operationId=JSON.parse(options.body).operation_id; throw Error('lost response');}
  assert.equal(url, `/api/media/operations/${operationId}`);
  return {ok:true,json:async()=>({operation_id:operationId,state:'succeeded',applied:['media:5'],skipped:[],failed:[],unknown:[]})};
 };
 try {
  await assert.rejects(media.setMediaFavorite(5,true), /lost response/);
  const record=await core.reconcileOperation(operationId);
  assert.equal(record.state,'committed'); assert.equal(writes,1);
 } finally {global.fetch=original;}
});
for (const remove of [false,true]) test(`media tag ${remove?'removal':'assignment'} sends operation identity and reads receipt`, async () => {
 const {tagging,tags} = runtime(); const original=global.fetch, id=crypto.randomUUID(), calls=[];
 const receipt={operation_id:id,state:'succeeded',applied:['media:5'],skipped:[],failed:[],unknown:[]};
 global.fetch=async (url,options)=>{calls.push([url,options.method]);return {ok:true,text:async()=>options.method ? '' : JSON.stringify(receipt)};};
 try {
  const result=remove ? await tags.removeTagFromMedia(5,9,id) : await tagging.assignTag('media',5,9,id);
  assert.deepEqual(result,receipt);
  assert.deepEqual(calls,[[`/api/tags/media/5/9?operation_id=${id}`,remove?'DELETE':'POST'],[`/api/media/operations/${id}`,undefined]]);
 } finally {global.fetch=original;}
});
