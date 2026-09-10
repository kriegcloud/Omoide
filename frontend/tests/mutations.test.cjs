const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const ts=require('typescript');
function environment(){
 const listeners=new Set(),events=[],cache=new Map();
 const bus={emit(event){events.push(event);for(const listener of listeners)listener(event)},subscribe(listener){listeners.add(listener);return()=>listeners.delete(listener)}};
 function load(relative){
  const file=path.resolve(__dirname,'../src',relative);
  if(cache.has(file))return cache.get(file).exports;
  const module={exports:{}};cache.set(file,module);
  const code=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText;
  new Function('require','module','exports',code)((id)=>{
   if(id.endsWith('/config'))return {API:''};
   if(id.endsWith('/mutationBus'))return {mutationBus:bus};
   if(id.startsWith('.'))return load(path.relative(path.resolve(__dirname,'../src'),path.resolve(path.dirname(file),id+'.ts')));
   return require(id);
  },module,module.exports);return module.exports;
 }
 return {load,bus,events};
}
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}};
const page=items=>({items,next_cursor:null,total:items.length});
test('delete service notifies every cached copy only after success',async()=>{
 const {load,events}=environment();const {deleteMediaRecord}=load('services/mediaActions.ts');
 global.fetch=async()=>({ok:false,status:500});await assert.rejects(deleteMediaRecord(7));assert.equal(events.length,0);
 global.fetch=async()=>({ok:true});await deleteMediaRecord(7);assert.deepEqual(events,[{type:'media:deleted',ids:[7]}]);
});
test('media deletion prunes all media lists without deleting an identically numbered person',async()=>{
 const {load,bus}=environment();const {useListStore:s}=load('stores/useListStore.ts');
 await s.getState().fetchInitial('images',async()=>page([{id:7,media_type:'image'},{id:8,media_type:'image'}]));
 await s.getState().fetchInitial('people-grid',async()=>page([{id:7,name:'Person'}]));
 bus.emit({type:'media:deleted',ids:[7]});assert.deepEqual(s.getState().lists.images.items.map(i=>i.id),[8]);assert.equal(s.getState().lists.images.total,1);assert.equal(s.getState().lists['people-grid'].items.length,1);
});
test('deletion while first page is pending cannot resurrect that media',async()=>{
 const {load,bus}=environment();const {useListStore:s}=load('stores/useListStore.ts');const d=deferred();
 const pending=s.getState().fetchInitial('images',()=>d.promise);bus.emit({type:'media:deleted',ids:[7]});d.resolve(page([{id:7,media_type:'image'},{id:8,media_type:'image'}]));await pending;
 assert.deepEqual(s.getState().lists.images.items.map(i=>i.id),[8]);
});
test('unfavorite removes Favorites membership while patching Images',async()=>{
 const {load,bus}=environment();const {useListStore:s}=load('stores/useListStore.ts');
 for(const key of ['favorites-newest','images'])await s.getState().fetchInitial(key,async()=>page([{id:7,media_type:'image',is_favorite:true}]));
 bus.emit({type:'media:updated',items:[{id:7,is_favorite:false}]});assert.equal(s.getState().lists['favorites-newest'].items.length,0);assert.equal(s.getState().lists.images.items[0].is_favorite,false);
});
test('missing-geo service rejects failed HTTP before returning error JSON',async()=>{
 const {load}=environment();global.fetch=async()=>({ok:false,status:500,json:async()=>({detail:'error'})});
 await assert.rejects(load('services/mapEditor.ts').getMissingGeoMedia(null));
});
test('bulk delete emits only processed ids',async()=>{
 const {load,events}=environment();global.fetch=async()=>({ok:true,json:async()=>({removed:1,processed_ids:[7],skipped_ids:[8],errors:[]})});
 await load('services/mediaActions.ts').bulkDeleteMedia([7,8],'DELETE_RECORDS');assert.deepEqual(events,[{type:'media:deleted',ids:[7]}]);
});
test('person creation invalidates people caches and assigns faces',async()=>{
 const {load,events}=environment();global.fetch=async()=>({ok:true,json:async()=>({person:{id:12}})});
 await load('services/faceActions.ts').createPersonFromFaces([3]);assert(events.some(e=>e.type==='person:created'&&e.id===12));assert(events.some(e=>e.type==='face:assigned'&&e.faceIds[0]===3));
});
test('partial duplicate 409 retains status and invalidates committed members and stats',async()=>{
 const {load,events}=environment();global.fetch=async()=>({ok:false,status:409,json:async()=>({detail:'some members survived'})});
 await assert.rejects(load('services/duplicates.ts').resolveDuplicates(3,'DELETE_FILES',7),error=>error.status===409);
 assert(events.some(e=>e.type==='list:invalidate'&&e.prefix===''));
});
test('task detail and dataset deletion reject non-success status',async()=>{
 const {load}=environment();global.fetch=async()=>({ok:false,status:503,json:async()=>({detail:'unavailable'})});
 await assert.rejects(load('services/task.ts').getTask(3));
 await assert.rejects(load('services/datasets.ts').deleteDataset(3));
});
test('generic pagination retains duplicate groups and scene results without outer ids',async()=>{
 const {load}=environment();const {useListStore:s}=load('stores/useListStore.ts');
 await s.getState().fetchInitial('duplicate-groups',async()=>({items:[{group_id:1},{group_id:2}],next_cursor:'2'}));
 await s.getState().loadMore('duplicate-groups',async()=>({items:[{group_id:3}],next_cursor:null}));
 assert.deepEqual(s.getState().lists['duplicate-groups'].items.map(i=>i.group_id),[1,2,3]);
});
test('deleting media does not delete an identically numbered tag',async()=>{
 const {load,bus}=environment();const {useListStore:s}=load('stores/useListStore.ts');
 await s.getState().fetchInitial('tags-all',async()=>page([{id:7,name:'tag',media:[],persons:[]}]));bus.emit({type:'media:deleted',ids:[7]});assert.equal(s.getState().lists['tags-all'].items.length,1);
});
test('pending Favorites response enforces membership after an unfavorite',async()=>{
 const {load,bus}=environment();const {useListStore:s}=load('stores/useListStore.ts');const pending=deferred();const request=s.getState().fetchInitial('favorites-newest',()=>pending.promise);
 bus.emit({type:'media:updated',items:[{id:8,is_favorite:false}]});pending.resolve(page([{id:8,is_favorite:true}]));await request;assert.equal(s.getState().lists['favorites-newest'].items.length,0);
});
test('successive partial patches compose over stale responses',async()=>{
 const {load,bus}=environment();const {useListStore:s}=load('stores/useListStore.ts');const pending=deferred();const request=s.getState().fetchInitial('images',()=>pending.promise);
 bus.emit({type:'media:updated',items:[{id:8,is_favorite:true}]});bus.emit({type:'media:updated',items:[{id:8,latitude:3}]});pending.resolve(page([{id:8,is_favorite:false}]));await request;assert.equal(s.getState().lists.images.items[0].is_favorite,true);assert.equal(s.getState().lists.images.items[0].latitude,3);
});
test('pending wrapped appearance response cannot resurrect deleted media',async()=>{
 const {load,bus}=environment();const {useListStore:s}=load('stores/useListStore.ts');const pending=deferred();const request=s.getState().fetchInitial('person-1-media-appearances',()=>pending.promise);
 bus.emit({type:'media:deleted',ids:[11]});pending.resolve(page([{type:'image',data:{id:11}}]));await request;assert.equal(s.getState().lists['person-1-media-appearances'].items.length,0);
});
