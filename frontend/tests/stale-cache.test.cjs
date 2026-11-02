const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const ts=require('typescript');
function environment(){
 const cache=new Map();
 function load(relative){
  const file=path.resolve(__dirname,'../src',relative);
  if(cache.has(file))return cache.get(file).exports;
  const module={exports:{}};cache.set(file,module);
  const code=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText;
  new Function('require','module','exports',code)(id=>id.startsWith('.')?load(path.relative(path.resolve(__dirname,'../src'),path.resolve(path.dirname(file),id+'.ts'))):require(id),module,module.exports);
  return module.exports;
 }
 return {load,...load('stores/optimisticOperations.ts')};
}

test('committed media patches mark filtered caches stale for membership and ordering',async()=>{
 const e=environment(),s=e.load('stores/useListStore.ts').useListStore;
 await s.getState().fetchInitial('favorites-date',async()=>({items:[],total:0,next_cursor:null}));
 e.load('stores/mutationBus.ts').mutationBus.emit({type:'media:updated',items:[{id:1,is_favorite:true}]});
 assert.equal(s.getState().lists['favorites-date'].stale,true);
});
test('media deletion prunes every cached duplicate variant and updates group totals',async()=>{
 const e=environment(),s=e.load('stores/useListStore.ts').useListStore;
 await s.getState().fetchInitial('duplicate-groups-hidden',async()=>({items:[{group_id:1,items:[{id:1},{id:2}]},{group_id:2,items:[{id:3},{id:4},{id:5}]}],total:2,next_cursor:null}));
 e.load('stores/mutationBus.ts').mutationBus.emit({type:'media:deleted',ids:[1,3]});
 assert.deepEqual(s.getState().lists['duplicate-groups-hidden'].items,[{group_id:2,items:[{id:4},{id:5}]}]);
 assert.equal(s.getState().lists['duplicate-groups-hidden'].total,1);
});
test('face changes invalidate media membership, people counts, appearances and timelines',async()=>{
 const e=environment(),s=e.load('stores/useListStore.ts').useListStore;
 const keys=['nopersons','people-grid-name','media-42-appearances','person-4-timeline','person-4-faces'];
 for(const key of keys)await s.getState().fetchInitial(key,async()=>({items:[],next_cursor:null}));
 e.load('stores/mutationBus.ts').mutationBus.emit({type:'face:assigned',faceIds:[1],personId:4,previous:{}});
 for(const key of keys)assert.equal(s.getState().lists[key].stale,true,key);
});
test('legacy removals update totals only for rows actually removed',async()=>{
 const e=environment(),s=e.load('stores/useListStore.ts').useListStore;
 await s.getState().fetchInitial('images',async()=>({items:[{id:1},{id:2}],total:10,next_cursor:null}));
 s.getState().removeItem('images',1);s.getState().removeItems('images',[1,2,99]);
 assert.equal(s.getState().lists.images.total,8);
});
test('cursor lists revalidate scoped invalidations and membership changes',async()=>{
 const e=environment(),{hooks,load,settle}=require('./detailRuntime.cjs'),r=hooks();r.api.useId=()=> 'stale-cursor';
 let listener,calls=0;const hook=load('hooks/useCursorList.ts',r,{'../stores/listReconciliation':e.load('stores/listReconciliation.ts'),'../stores/optimisticOperations':e,'../stores/mutationBus':{mutationBus:{subscribe:fn=>{listener=fn;return ()=>{};}}}}).useCursorList;
 const fetcher=async()=>{calls++;return {items:[],total:0,next_cursor:null}};
 const options={queryKey:'review:untagged',entityKind:'media'},render=()=>r.render(()=>hook(fetcher,0,options));
 render();await settle();render();listener({type:'list:invalidate',prefix:'review:'});await settle();render();assert.equal(calls,2);
 listener({type:'media:updated',items:[{id:1}]});await settle();render();assert.equal(calls,3);r.unmount();
});
test('scene collections re-read after another component commits a mutation',async()=>{
 const {hooks,load,settle}=require('./detailRuntime.cjs'),r=hooks();let refresh,calls=0;
 const Scene=load('components/SceneManager.tsx',r,{'../context/UndoContext':{useMutationRefresh:(types,fn)=>{refresh=fn;}},'../services/scene':{getScenes:async()=>{calls++;return [];}}}).SceneManager;
 const render=()=>r.render(()=>Scene({mediaId:1,persons:[],videoRef:{current:null}}));
 render();await settle();render();assert.equal(typeof refresh,'function');await refresh();assert.equal(calls,2);r.unmount();
});
test('a second mutation during a coalesced cache refresh triggers a newer authoritative GET',async()=>{
 const e=environment(),s=e.load('stores/useListStore.ts'),events=e.load('stores/mutationBus.ts').mutationBus,wait={};wait.promise=new Promise(r=>wait.resolve=r);let calls=0;
 await s.useListStore.getState().fetchInitial('images',()=>++calls===1?Promise.resolve({items:[{id:1}],next_cursor:null}):calls===2?wait.promise:Promise.resolve({items:[{id:3}],next_cursor:null}));
 const refreshing=s.refreshCachedList('images');events.emit({type:'media:updated',items:[{id:1,is_favorite:true}]});
 wait.resolve({items:[{id:2}],next_cursor:null});await refreshing;
 assert.equal(calls,3);assert.deepEqual(s.useListStore.getState().lists.images.items,[{id:3}]);
});
test('statistics reread tag album and event totals on their committed domain events',()=>{
 const {hooks,load}=require('./detailRuntime.cjs'),r=hooks();let dispatch,calls=0;
 const ui=new Proxy({useTheme:()=>({palette:{mode:'light'}})},{get:(v,k)=>v[k]??k});
 const Page=load('pages/StatisticsPage.tsx',r,{'@mui/material':ui,'../context/UndoContext':{useUndoRefresh:()=>{},useMutationRefresh:(types,fn)=>{dispatch=e=>{if(types.includes(e.type))fn();};}},'../services/features':{getLibraryStats:()=>{calls++;return new Promise(()=>{});}}}).default;
 const render=()=>r.render(Page);render();for(const type of ['tag:changed','album:changed','events:changed']){dispatch({type});render();}
 assert.equal(calls,4);r.unmount();
});
