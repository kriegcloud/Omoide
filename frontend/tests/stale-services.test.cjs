const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const ts=require('typescript');
function environment(result={}) {
 const events=[],cache=new Map();
 function load(relative){
  const file=path.resolve(__dirname,'../src',relative);
  if(cache.has(file))return cache.get(file).exports;
  const module={exports:{}};cache.set(file,module);
  const code=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
  new Function('require','module','exports',code)(id=>{
   if(id.endsWith('/config'))return {API:''};
   if(id.endsWith('/mutationBus'))return {mutationBus:{emit:event=>events.push(event)}};
   if(id.startsWith('.'))return load(path.relative(path.resolve(__dirname,'../src'),path.resolve(path.dirname(file),id+'.ts')));
   return require(id);
  },module,module.exports);return module.exports;
 }
 global.fetch=async()=>({ok:true,status:200,json:async()=>result,text:async()=>JSON.stringify(result)});
 return {events,load};
}
test('album create, membership and partial bulk delete publish committed changes',async()=>{
 let env=environment({id:4});await env.load('services/features.ts').createAlbum('test');assert(env.events.some(e=>e.type==='album:changed'&&e.ids[0]===4));
 env=environment({id:4});await env.load('services/features.ts').removeMediaFromAlbum(4,[7,8]);assert(env.events.some(e=>e.type==='album:changed'&&e.ids[0]===4));assert(env.events.some(e=>e.type==='list:invalidate'&&e.prefix===''));
 env=environment({deleted_ids:[4],skipped_ids:[5]});await env.load('services/albums.ts').deleteAlbumsBulk([4,5]);assert.deepEqual(env.events.find(e=>e.type==='album:changed').ids,[4]);
});
test('tag mutations refresh tag detail and filtered media membership after success',async()=>{
 let env=environment({id:3});await env.load('services/tagging.ts').createTag('tag');assert(env.events.some(e=>e.type==='tag:changed'&&e.ids[0]===3));
 for(const [file,name,args] of [['tagging','assignTag',['media',7,3]],['tagActions','removeTagFromMedia',[7,3]],['tagActions','removeTagFromPerson',[8,3]],['tagActions','deleteTag',[3]]]){
  env=environment();await env.load(`services/${file}.ts`)[name](...args);assert(env.events.some(e=>e.type==='tag:changed'&&e.ids[0]===3),name);assert(env.events.some(e=>e.type==='list:invalidate'&&e.prefix===''),name);
 }
 env=environment({deleted_ids:[3],skipped_ids:[4]});await env.load('services/tag.ts').deleteTagsBulk([3,4]);assert.deepEqual(env.events.find(e=>e.type==='tag:changed').ids,[3]);
});
test('review partial deletes publish processed subset and count-only success invalidates',async()=>{
 for(const result of [{removed:1,processed_ids:[7],skipped_ids:[8]},{removed:1}]){
  const {load,events}=environment(result);await load('services/review.ts').resolveReview('/api/blur',{},'DELETE_RECORDS',{mediaIds:[7,8]});
  assert(events.some(e=>e.type==='review:changed'&&e.scope==='/api/blur'));
  assert(result.processed_ids?events.some(e=>e.type==='media:deleted'&&e.ids.join() === '7'):events.some(e=>e.type==='list:invalidate'&&e.prefix===''));
 }
});
test('face message-only success never claims requested IDs as committed subset',async()=>{
 for(const [name,args,result] of [['assignFace',[[7,8],2],{message:'ok'}],['deleteFace',[[7,8]],{message:'ok'}],['detachFace',[[7,8]],{message:'ok'}],['createPersonFromFaces',[[7,8]],{id:2}]]){
  const {load,events}=environment(result);await load('services/faceActions.ts')[name](...args);
  assert(!events.some(e=>['face:assigned','face:deleted','face:detached'].includes(e.type)),name);
  assert(events.some(e=>e.type==='list:invalidate'&&e.prefix===''),name);
 }
});
test('dataset writes and repair writes notify peers only after response success',async()=>{
 for(const [file,name,args,result,type] of [['datasets','updateDataset',[4,{name:'new'}],{id:4},'dataset:changed'],['datasets','addDatasetItems',[4,[7]],{added_ids:[7],skipped_ids:[]},'dataset:changed'],['datasets','deleteDataset',[4],{},'dataset:changed'],['repairs','startRepair',[7,'background'],{id:'job'},'repairs:changed'],['repairs','cancelRepair',['job'],{id:'job'},'repairs:changed']]){
  const {load,events}=environment(result);await load(`services/${file}.ts`)[name](...args);assert(events.some(e=>e.type===type),name);
 }
});
test('rejected album tag review dataset repair and face writes emit no success event',async()=>{
 for(const [file,name,args] of [['features','createAlbum',['test']],['tagActions','deleteTag',[3]],['review','resolveReview',['/api/blur',{},'DELETE_RECORDS',{mediaIds:[7]}]],['datasets','deleteDataset',[4]],['repairs','cancelRepair',['job']],['faceActions','assignFace',[[7],2]]]){
  const {load,events}=environment();global.fetch=async()=>({ok:false,status:503,json:async()=>({detail:'offline'})});await assert.rejects(load(`services/${file}.ts`)[name](...args));assert.deepEqual(events,[],name);
 }
});
test('event bulk deletion publishes only deleted events and pair decisions refresh people',async()=>{
 let env=environment({deleted_ids:[2],skipped_ids:[3]});await env.load('services/events.ts').deleteEventsBulk([2,3]);assert.deepEqual(env.events.find(e=>e.type==='events:changed').ids,[2]);
 env=environment({id:5});await env.load('services/personActions.ts').createPairDecision({person_a_id:2,person_b_id:3,decision:'not_same'});assert(env.events.some(e=>e.type==='person:changed'));
 env=environment();await env.load('services/personActions.ts').deletePairDecision(5);assert(env.events.some(e=>e.type==='person:changed'));
});
test('scene and timeline writes publish shared media and person refreshes',async()=>{
 for(const [file,name,args,type] of [['scene','createScene',[7,{start_time:0}],'media:updated'],['scene','deleteScene',[7,2],'media:updated'],['timeline','createTimelineEvent',[3,{event_date:new Date(2026,1,1)}],'person:changed'],['timeline','updateTimelineEvent',[3,2,{}],'person:changed'],['timeline','deleteTimelineEvent',[3,2],'person:changed']]){
  const {load,events}=environment({id:2});await load(`services/${file}.ts`)[name](...args);assert(events.some(e=>e.type===type),name);
 }
});
test('repair polling bridges a terminal transition once using the returned result media id',async()=>{
 const {load,events}=environment({items:[{id:'job',status:'running',media_id:7}],next_cursor:null});const service=load('services/repairs.ts');await service.listRepairs();assert.deepEqual(events,[]);
 global.fetch=async()=>({ok:true,json:async()=>({items:[{id:'job',status:'succeeded',media_id:7,result_media_id:12}],next_cursor:null})});await service.listRepairs();await service.listRepairs();
 assert.equal(events.filter(e=>e.type==='repairs:changed').length,1);assert.deepEqual(events.find(e=>e.type==='media:updated')?.items,[{id:12}]);assert.equal(events.filter(e=>e.type==='list:invalidate'&&e.prefix==='').length,1);
});
test('suggested face assignments notify every owner and count consumer after commit',async()=>{
 const {load,events}=environment({assigned:1,skipped:[{face_id:8,reason:'face_already_assigned'}]});
 await load('services/face.ts').assignSuggestedFaces([{face_id:7,person_id:2},{face_id:8,person_id:3}]);
 assert(events.some(e=>e.type==='list:invalidate'&&e.prefix===''));
 assert(events.some(e=>e.type==='person:changed'));
});
test('creating a folder invalidates other open folder navigation controls',async()=>{
 const {load,events}=environment({path:'new',name:'new'});await load('services/mediaActions.ts').createMediaFolder('','new');
 assert(events.some(e=>e.type==='list:invalidate'&&e.prefix===''));
});
test('person detail errors retain HTTP status so deleted containers can leave the view',async()=>{
 const {load}=environment();global.fetch=async()=>({ok:false,status:404});
 await assert.rejects(load('services/person.ts').getPerson('4'),error=>error.status===404);
});
