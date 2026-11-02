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
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}};
const tick=()=>new Promise(r=>setImmediate(r));
const receipt=(id,state='succeeded',applied=['media:1'],extra={})=>({operation_id:id,state,applied,skipped:[],failed:[],unknown:[],...extra});
const patch=(key='media:1',kind='remove',extra={})=>({kind,key,...extra});
function command(id,d,extra={}){return {id,label:id,input:{ids:[1]},resources:['media:1'],patches:[patch()],request:()=>d.promise,receipt:r=>r,...extra}}
test('disjoint rollback removes only its own overlay',async()=>{
 const e=environment(),a=deferred(),b=deferred();
 const one=e.startOperation(command('a',a)),two=e.startOperation(command('b',b,{resources:['media:2'],patches:[patch('media:2')]}));
 assert.deepEqual(e.projectList('images','media',[{id:1},{id:2}],2).items,[]);
 a.reject(new e.DefinitiveMutationError('denied'));await one.done;
 assert.deepEqual(e.projectList('images','media',[{id:1},{id:2}],2).items,[{id:1}]);
 b.resolve(receipt('b','succeeded',['media:2']));await two.done;
});
test('same entity requests serialize while newer intent applies immediately',async()=>{
 const e=environment(),a=deferred(),b=deferred(),calls=[];
 const one=e.startOperation(command('a',a,{patches:[patch('media:1','fields',{values:{is_favorite:true}})],request:()=>{calls.push('a');return a.promise}}));
 const two=e.startOperation(command('b',b,{patches:[patch('media:1','fields',{values:{is_favorite:false}})],request:()=>{calls.push('b');return b.promise}}));
 assert.equal(e.projectEntity('media:1',{id:1,is_favorite:true}).is_favorite,false);
 await tick();assert.deepEqual(calls,['a']);a.resolve(receipt('a'));await one.done;await tick();assert.deepEqual(calls,['a','b']);b.resolve(receipt('b'));await two.done;
});
test('late GET overlays pending and committed intent until a newer authoritative read',async()=>{
 const e=environment(),d=deferred(),fence=e.operationVersion();const h=e.startOperation(command('a',d));
 e.acknowledgeQuery('images',fence);assert.equal(e.projectList('images','media',[{id:1}],1).items.length,0);
 d.resolve(receipt('a'));await h.done;e.acknowledgeQuery('images',fence);assert.equal(e.projectList('images','media',[{id:1}],1).items.length,0);
 e.acknowledgeQuery('images',e.operationVersion());assert.equal(e.projectList('images','media',[{id:2}],1).total,1);
});
test('global failure delivery survives view unsubscribe and later subscription',async()=>{
 const e=environment(),d=deferred();const off=e.optimisticOperations.subscribe(()=>{});const h=e.startOperation(command('a',d));off();d.reject(new e.DefinitiveMutationError('denied'));await h.done;
 assert.equal(e.optimisticOperations.getSnapshot()[0].state,'rejected');assert.match(e.optimisticOperations.getSnapshot()[0].error,/denied/);
});
test('unknown keeps overlay and blocks successor until lookup reconciles, without replay',async()=>{
 const e=environment(),d=deferred(),next=deferred();let calls=0;
 const h=e.startOperation(command('a',d));const second=e.startOperation(command('b',next,{request:()=>{calls++;return next.promise}}));
 d.reject(new TypeError('network lost'));assert.equal((await h.done).state,'unknown');await tick();assert.equal(calls,0);
 await e.reconcileOperation('a',async()=>receipt('a','failed',[],{failed:[{key:'media:1',code:'denied',message:'denied'}]}));
 await tick();assert.equal(calls,1);next.resolve(receipt('b'));await second.done;
});
test('successful mutation plus failed refresh stays committed and reports refresh separately',async()=>{
 const e=environment(),d=deferred();let commits=0;const h=e.startOperation(command('a',d,{commit:()=>{commits++},refresh:async()=>{throw Error('refresh offline')}}));
 d.resolve(receipt('a'));assert.equal((await h.done).state,'committed');await tick();assert.equal(commits,1);assert.match(e.optimisticOperations.getSnapshot()[0].refreshError,/refresh offline/);
});
test('partial receipt restores failures and skips but retains applied and unknown members',async()=>{
 const e=environment(),d=deferred();const h=e.startOperation(command('a',d,{resources:['media:1','media:2','media:3'],patches:[patch(),patch('media:2'),patch('media:3')]}));
 d.resolve(receipt('a','partial',['media:1'],{failed:[{key:'media:2',code:'denied',message:'denied'}],skipped:[{key:'media:3',reason:'missing'}]}));
 assert.equal((await h.done).state,'partially_committed');assert.deepEqual(e.projectList('images','media',[{id:1},{id:2},{id:3}],3),{items:[{id:2},{id:3}],total:2});
});
test('count removal is idempotent across overlapping operations and repeated reads',async()=>{
 const e=environment(),a=deferred(),b=deferred();const one=e.startOperation(command('a',a)),two=e.startOperation(command('b',b));
 for(let i=0;i<3;i++)assert.equal(e.projectList('images','media',[{id:1},{id:2}],10).total,9);
 a.resolve(receipt('a'));await one.done;b.resolve(receipt('b'));await two.done;assert.equal(e.projectList('images','media',[{id:1}],10).total,9);
});
test('Undo waits for acknowledged original and builds inverse only from actual receipt',async()=>{
 const e=environment(),d=deferred(),calls=[];const h=e.startOperation(command('a',d));
 const undo=e.queueOperationUndo(h.id,r=>{calls.push(r.applied);return command('inverse',{promise:Promise.resolve(receipt('inverse'))})});
 await tick();assert.deepEqual(calls,[]);d.resolve(receipt('a'));await h.done;await undo;assert.deepEqual(calls,[['media:1']]);
});
test('runtime receipt validation rejects malformed, mismatched and overlapping members',()=>{
 const e=environment();assert.throws(()=>e.parseMutationReceipt({operation_id:'a',state:'success'}));
 assert.throws(()=>e.parseMutationReceipt(receipt('b'),'a'));
 assert.throws(()=>e.parseMutationReceipt(receipt('a','partial',['media:1'],{unknown:['media:1']})));
});
test('query scoped insert and derived pending do not leak into unrelated queries',async()=>{
 const e=environment(),d=deferred();const h=e.startOperation(command('a',d,{patches:[patch('media:1','insert',{query:'album:4',item:{id:1}}),patch('media:1','derived',{query:'album:4',field:'count'})]}));
 assert.equal(e.projectList('album:4','media',[],0).items.length,1);assert.equal(e.projectList('album:5','media',[],0).items.length,0);
 d.reject(new e.DefinitiveMutationError('denied'));await h.done;
});
test('committed events are once-only on repeated receipt lookup and counts do not double-decrement base',async()=>{
 const e=environment(),d=deferred(),events=[];
 const off=e.load('stores/mutationBus.ts').mutationBus.subscribe(event=>events.push(event));
 const h=e.startOperation(command('a',d,{events:(r,keys)=>[{type:'media:deleted',ids:keys.map(k=>Number(k.split(':')[1]))}]}));
 d.resolve(receipt('a'));await h.done;await e.reconcileOperation('a',async()=>receipt('a'));off();
 assert.equal(events.length,1);assert.equal(e.projectList('images','media',[{id:2}],9).total,9);
});
test('cached refresh retains old rows through loading and failure',async()=>{
 const e=environment(),{useListStore:s,refreshCachedList}=e.load('stores/useListStore.ts'),d=deferred();let calls=0;
 await s.getState().fetchInitial('images',()=>++calls===1?Promise.resolve({items:[{id:1}],next_cursor:null,total:1}):d.promise);
 const refresh=refreshCachedList('images');assert.equal(s.getState().lists.images.items.length,1);
 d.reject(Error('offline'));await assert.rejects(refresh,/offline/);assert.equal(s.getState().lists.images.items.length,1);
});
test('cache eviction preserves active and pending queries and removes reverse-index metadata',async()=>{
 const e=environment(),s=e.load('stores/useListStore.ts'),d=deferred();
 for(const key of ['images','album:4','videos'])await s.useListStore.getState().fetchInitial(key,async()=>({items:[{id:1}],next_cursor:null}));
 const unpin=s.pinCachedList('images');e.registerQueryEntities('album:4',['media:1']);
 const h=e.startOperation(command('a',d,{patches:[patch('media:1','remove',{query:'album:4'})]}));
 assert.deepEqual(s.evictCachedLists(0),['videos']);assert(e.affectedQueries(['media:1']).includes('album:4'));
 d.reject(new e.DefinitiveMutationError('denied'));await h.done;s.evictCachedLists(0);assert.deepEqual(e.affectedQueries(['media:1']),[]);unpin();
});
test('multiple adapters for a query retain independent entity registrations',()=>{
 const e=environment();const offA=e.registerQueryEntities('images',['media:1']),offB=e.registerQueryEntities('images',['media:2']);
 offA();assert.deepEqual(e.affectedQueries(['media:2']),['images']);offB();assert.deepEqual(e.affectedQueries(['media:2']),[]);
});
test('targeted refresh scheduler coalesces callers and preserves a failed refresh for each caller',async()=>{
 const e=environment(),d=deferred();let calls=0;const off=e.registerQueryRefresh('images',()=>{calls++;return d.promise});
 const one=e.revalidateQueries(['images','images']),two=e.revalidateQueries(['images']);await tick();assert.equal(calls,1);
 d.reject(Error('offline'));await assert.rejects(one,/offline/);await assert.rejects(two,/offline/);off();
});
test('malformed successful HTTP response is unknown and never blindly restores destructive intent',async()=>{
 const e=environment(),d=deferred();const h=e.startOperation(command('a',d));d.resolve({receipt:'bad'});
 assert.equal((await h.done).state,'unknown');assert.equal(e.projectList('images','media',[{id:1}]).items.length,0);
});
test('repeated committed removal during GET advances its revision fence',()=>{
 const e=environment(),{ListRevision}=e.load('stores/listReconciliation.ts'),j=new ListRevision();j.remove([1]);const started=j.revision;j.remove([1]);
 assert.deepEqual(j.reconcile([{id:1}],started,true),[]);
});
test('refresh supersedes an older in-flight GET without letting it overwrite fresh rows',async()=>{
 const e=environment(),s=e.load('stores/useListStore.ts'),old=deferred(),fresh=deferred();let calls=0;
 const fetcher=()=>++calls===1?old.promise:fresh.promise;
 const first=s.useListStore.getState().fetchInitial('images',fetcher);
 const refresh=s.refreshCachedList('images');fresh.resolve({items:[{id:2}],next_cursor:null});await refresh;
 old.resolve({items:[{id:1}],next_cursor:null});await first;
 assert.deepEqual(s.useListStore.getState().lists.images.items,[{id:2}]);
});
test('HTTP 409 uses durable lookup rather than treating status as definitive rejection',async()=>{
 const e=environment();let lookedUp=0;
 const r=await e.readMutationResponse({ok:false,status:409},'a',async id=>{lookedUp++;return receipt(id,'partial',['media:1'],{failed:[{key:'media:2',code:'readonly',message:'read only'}]})});
 assert.equal(r.state,'partial');assert.equal(lookedUp,1);
});
test('unknown member receipt preserves only uncertain/successful overlays and holds ordering',async()=>{
 const e=environment(),d=deferred(),next=deferred();let calls=0;
 const h=e.startOperation(command('a',d,{resources:['media:1','media:2','media:3'],patches:[patch(),patch('media:2'),patch('media:3')]}));
 const queued=e.startOperation(command('b',next,{resources:['media:3'],patches:[],request:()=>{calls++;return next.promise}}));
 d.resolve(receipt('a','unknown',['media:1'],{unknown:['media:3'],failed:[{key:'media:2',code:'refused',message:'refused'}]}));
 await h.done;assert.deepEqual(e.projectList('images','media',[{id:1},{id:2},{id:3}]).items,[{id:2}]);await tick();assert.equal(calls,0);
 await e.reconcileOperation('a',async()=>receipt('a','partial',['media:1','media:3'],{failed:[{key:'media:2',code:'refused',message:'refused'}]}));
 await tick();assert.equal(calls,1);next.resolve(receipt('b','succeeded',['media:3']));await queued.done;
});
test('concurrent cached refresh callers share one transport and failure',async()=>{
 const e=environment(),s=e.load('stores/useListStore.ts'),d=deferred();let calls=0;
 await s.useListStore.getState().fetchInitial('images',()=>++calls===1?Promise.resolve({items:[{id:1}],next_cursor:null}):d.promise);
 const a=s.refreshCachedList('images'),b=s.refreshCachedList('images');assert.equal(calls,2);
 d.reject(Error('offline'));await assert.rejects(a,/offline/);await assert.rejects(b,/offline/);
});
test('disjoint transport concurrency is bounded while every overlay applies immediately',async()=>{
 const e=environment(),ds=Array.from({length:6},deferred);let started=0;
 const handles=ds.map((d,i)=>e.startOperation(command(String(i),d,{resources:[`media:${i}`],patches:[patch(`media:${i}`)],request:()=>{started++;return d.promise}})));
 assert.equal(e.projectList('images','media',ds.map((_,id)=>({id}))).items.length,0);
 await tick();assert.equal(started,4);
 ds.forEach((d,i)=>d.resolve(receipt(String(i),'succeeded',[`media:${i}`])));await Promise.all(handles.map(h=>h.done));assert.equal(started,6);
});
test('dismissal and repeated receipt lookup cannot resurrect overlays after query acknowledgement',async()=>{
 const e=environment(),d=deferred();const h=e.startOperation(command('a',d,{patches:[patch('media:1','fields',{values:{is_favorite:true}})]}));
 d.resolve(receipt('a'));await h.done;e.acknowledgeQuery('images',e.operationVersion());
 e.optimisticOperations.dismiss('a');await e.reconcileOperation('a',async()=>receipt('a'));
 assert.equal(e.projectList('images','media',[{id:1,is_favorite:false}]).items[0].is_favorite,false);
});
test('scoped removal does not double-count a committed base deletion or guess unloaded membership',async()=>{
 const e=environment(),d=deferred();const h=e.startOperation(command('a',d,{patches:[patch('media:1','remove',{query:'album:4'})]}));
 assert.equal(e.projectList('album:4','media',[{id:1}],5).total,4);
 d.resolve(receipt('a'));await h.done;assert.equal(e.projectList('album:4','media',[{id:2}],4).total,4);
});
test('invalidation supersedes old pending cached read while preserving displayed data',async()=>{
 const e=environment(),s=e.load('stores/useListStore.ts'),old=deferred();
 const pending=s.useListStore.getState().fetchInitial('images',()=>old.promise);
 s.invalidateCachedLists('images');await s.useListStore.getState().fetchInitial('images',async()=>({items:[{id:2}],next_cursor:null}));
 old.resolve({items:[{id:1}],next_cursor:null});await pending;assert.deepEqual(s.useListStore.getState().lists.images.items,[{id:2}]);
});
test('cursor hook adapter reapplies overlay to late GET and retains route-independent errors after unmount',async()=>{
 const e=environment(),{hooks,load,settle}=require('./detailRuntime.cjs'),runtime=hooks(),page=deferred(),request=deferred();runtime.api.useId=()=> 'optimistic-cursor';
 const hook=load('hooks/useCursorList.ts',runtime,{
  '../stores/optimisticOperations':e,
  '../stores/queryKeys':e.load('stores/queryKeys.ts'),
  '../stores/listReconciliation':e.load('stores/listReconciliation.ts'),
 }).useCursorList;
 const fetcher=()=>page.promise,options={queryKey:'review:fixture',entityKind:'media'};
 const render=()=>runtime.render(()=>hook(fetcher,0,options));render();
 const h=e.startOperation(command('a',request));page.resolve({items:[{id:1},{id:2}],total:2,next_cursor:null});await settle();
 assert.deepEqual(render().items,[{id:2}]);assert.equal(render().total,1);runtime.unmount();
 request.reject(new e.DefinitiveMutationError('navigation-safe error'));await h.done;assert.equal(e.optimisticOperations.getSnapshot()[0].error,'navigation-safe error');
});
test('cursor grid revalidation keeps existing cards and optional overlay hides pending cards',async()=>{
 const e=environment(),{hooks,load,settle,component,noop}=require('./detailRuntime.cjs'),runtime=hooks(),reload=deferred(),request=deferred();let calls=0;
 const Grid=load('components/CursorMediaGrid.tsx',runtime,{
  '../stores/optimisticOperations':e,
  '../stores/listReconciliation':e.load('stores/listReconciliation.ts'),
  '../context/SelectionContext':{useSelection:()=>({selectedIds:new Set(),setSelected:noop,beginSelecting:noop,clear:noop})},
  '../hooks/useMarqueeSelection':{useGridSelection:()=>({})},
 }).CursorMediaGrid;
 const fetcher=()=>++calls===1?Promise.resolve({items:[{id:1}],next_cursor:null}):reload.promise;
 const render=token=>runtime.render(()=>Grid({listKey:'album:fixture',fetcher,refreshToken:token,optimistic:true}));
 render(0);await settle();assert(component(render(0),'react-masonry-css'));render(1);assert(component(render(1),'react-masonry-css'));
 const h=e.startOperation(command('a',request));assert.equal(component(render(1),'react-masonry-css'),undefined);
 request.reject(new e.DefinitiveMutationError('denied'));await h.done;assert(component(render(1),'react-masonry-css'));
 runtime.unmount();reload.resolve({items:[{id:1}],next_cursor:null});await settle();
});
test('legacy cursor removal is idempotent for repeated explicit ids',async()=>{
 const e=environment(),{hooks,load,settle}=require('./detailRuntime.cjs'),runtime=hooks();runtime.api.useId=()=> 'cursor-idempotence';
 const hook=load('hooks/useCursorList.ts',runtime,{'../stores/listReconciliation':e.load('stores/listReconciliation.ts')}).useCursorList;
 const fetcher=async()=>({items:[{id:1},{id:2}],next_cursor:null,total:10}),render=()=>runtime.render(()=>hook(fetcher));
 render();await settle();render().removeItems([1]);render().removeItems([1]);assert.equal(render().total,9);runtime.unmount();
});
