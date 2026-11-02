const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const ts=require('typescript');
const {deferred}=require('./detailRuntime.cjs');
function environment(fetcher){
 const cache=new Map(),events=[];
 function load(relative){
  const file=path.resolve(__dirname,'../src',relative);
  if(cache.has(file))return cache.get(file).exports;
  const module={exports:{}};cache.set(file,module);
  const code=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
  new Function('require','module','exports','fetch',code)(id=>{
   if(id.endsWith('/config'))return {API:''};
   if(id.endsWith('/mutationBus'))return {mutationBus:{emit:e=>events.push(e)}};
   if(id.startsWith('.'))return load(path.relative(path.resolve(__dirname,'../src'),path.resolve(path.dirname(file),id+'.ts')));
   return require(id);
  },module,module.exports,fetcher);return module.exports;
 }
 return {api:load('services/mediaActions.ts'),core:load('stores/optimisticOperations.ts'),events};
}
const tick=()=>new Promise(r=>setImmediate(r));
const response=value=>({ok:true,json:async()=>value});
test('favorite intent is immediate, shared, and serialized across card/detail callers',async()=>{
 const a=deferred(),b=deferred(),calls=[];const e=environment((url,opts)=>{calls.push(JSON.parse(opts.body));return calls.length===1?a.promise:b.promise});
 const one=e.api.setMediaFavorite(1,true),two=e.api.setMediaFavorite(1,false);
 assert.equal(e.core.projectEntity('media:1',{id:1,is_favorite:true}).is_favorite,false);
 await tick();assert.equal(calls.length,1);
 a.resolve(response({id:1,is_favorite:true}));await one;await tick();assert.equal(calls.length,2);
 assert.equal(e.core.projectEntity('media:1',{id:1,is_favorite:true}).is_favorite,false);
 b.resolve(response({id:1,is_favorite:false}));await two;
});
test('delete hides media before response and retains global unknown feedback after transport loss',async()=>{
 const d=deferred(),e=environment(()=>d.promise);const promise=e.api.deleteMediaFile(2);
 assert.deepEqual(e.core.projectList('images','media',[{id:2}],1).items,[]);
 await tick();d.reject(Error('lost response'));await assert.rejects(promise,/lost response/);
 assert.equal(e.core.optimisticOperations.getSnapshot()[0].state,'unknown');assert.deepEqual(e.events,[]);
});
test('bulk delete snapshots inputs and restores only acknowledged skips/errors',async()=>{
 const d=deferred(),calls=[],e=environment((url,options)=>{calls.push(JSON.parse(options.body));return d.promise});
 const ids=[1,2,3];const promise=e.api.bulkDeleteMedia(ids,'DELETE_FILES');ids.push(4);
 assert.equal(e.core.projectList('images','media',[{id:1},{id:2},{id:3}],3).items.length,0);
 await tick();assert.deepEqual(calls[0].media_ids,[1,2,3]);
 d.resolve(response({removed:1,processed_ids:[1],skipped_ids:[2],errors:[{id:3,error:'denied'}]}));await promise;
 assert.deepEqual(e.core.projectList('images','media',[{id:1},{id:2},{id:3}],3).items,[{id:2},{id:3}]);
 assert.equal(e.core.optimisticOperations.getSnapshot()[0].state,'partially_committed');
});
test('move and edit publish independent per-operation progress without inventing destination or edit result',async()=>{
 const d=deferred(),e=environment(()=>d.promise);
 const move=e.api.moveMedia(1,'/dest'),edit=e.api.editMedia(2,{ops:[],mode:'overwrite',design_state:null});
 assert.equal(e.core.optimisticOperations.getSnapshot().length,2);
 assert.deepEqual(e.core.projectEntity('media:1',{id:1,folder:'/old'}),{id:1,folder:'/old'});
 d.resolve(response({id:1,media:{id:2}}));await Promise.all([move,edit]);
});
const {hooks,load,component,settle,listStore,noop}=require('./detailRuntime.cjs');
test('detail delete dismisses confirmation and navigates while the response is delayed',async()=>{
 const runtime=hooks(),d=deferred(),navigated=[];
 const Page=load('pages/MediaDetailPage.tsx',runtime,{
  'react-router-dom':{useParams:()=>({id:'1'}),useLocation:()=>({key:'1',state:{mediaListKey:'images'}}),useNavigate:()=>to=>navigated.push(to)},
  '../services/media':{getMedia:async()=>({media:{id:1,duration:10},persons:[],orphans:[]})},
  '../services/mediaActions':{deleteMediaRecord:()=>d.promise},'../stores/useListStore':listStore().module,
 }).default;
 const render=()=>runtime.render(Page);render();await settle();component(render(),'MediaHeader').props.onOpenDialog('deleteRecord');
 const done=component(render(),'ActionDialogs').props.onConfirmDeleteRecord();
 assert.deepEqual(navigated,['/']);assert.equal(component(render(),'ActionDialogs')?.props.dialogType ?? null,null);
 d.resolve();await done;
});
test('selection confirmation closes immediately while retaining the opened target snapshot',async()=>{
 const runtime=hooks(),d=deferred(),calls=[];let hotkey;
 const Dialogs=load('hotkeys/SelectionHotkeyDialogs.tsx',runtime,{
  './useHotkey':{useHotkeys:(_keys,handler)=>{hotkey=handler}},'./keymap':{selectionBindings:[{key:'Delete'}]},
  '../services/mediaActions':{bulkDeleteMedia:(ids,action)=>{calls.push({ids,action});return d.promise}},
  '../components/BulkResolveToolbar':{resolveConfirmCopy:()=>({title:'Delete'})},
 }).SelectionHotkeyDialogs;
 let ids=[1,2];const render=()=>runtime.render(()=>Dialogs({mediaIds:ids,includeDelete:true}));render();hotkey({key:'Delete'});ids=[3];
 const done=component(render(),'../components/ConfirmDialog').props.onConfirm();
 assert.equal(component(render(),'../components/ConfirmDialog').props.open,false);assert.deepEqual(calls,[{ids:[1,2],action:'DELETE_FILES'}]);
 d.resolve({removed:2,processed_ids:[1,2],skipped_ids:[],errors:[]});await done;
});
test('favorite validation rejection restores only its intent and reports the error globally',async()=>{
 const e=environment(async()=>({ok:false,status:422}));
 const promise=e.api.setMediaFavorite(1,true);
 assert.equal(e.core.projectEntity('media:1',{id:1,is_favorite:false}).is_favorite,true);
 await assert.rejects(promise,/favorite/);
 assert.equal(e.core.projectEntity('media:1',{id:1,is_favorite:false}).is_favorite,false);
 assert.equal(e.core.optimisticOperations.getSnapshot()[0].state,'rejected');
});
test('selection move snapshots at open, closes immediately, and leaves a later selection intact',async()=>{
 const runtime=hooks(),d=deferred(),calls=[],selection={selectedIds:new Set([1,2]),clear:()=>{throw Error('new selection cleared')},loadedCount:2};
 const Bar=load('components/SelectionActionBar.tsx',runtime,{
  '../context/SelectionContext':{useSelection:()=>selection},
  '../hotkeys/useHotkey':{useHotkeys:noop,useHotkey:noop},'../hotkeys/keymap':{selectionBindings:[]},
  '../stores/useListStore':listStore().module,'../stores/useLastEditStore':{useLastEditStore:f=>f({ops:null})},
  '../services/mediaActions':{bulkMoveMedia:(ids,destination)=>{calls.push({ids,destination});return d.promise}},
  'react-router-dom':{useLocation:()=>({pathname:'/images'}),matchPath:()=>null},
 }).SelectionActionBar;
 const {find}=require('./detailRuntime.cjs');const render=()=>runtime.render(Bar);
 find(render(),node=>node.type==='Button' && node.props.children==='Move to folder').props.onClick();selection.selectedIds=new Set([3]);
 component(render(),'./FolderPickerDialog').props.onConfirm('/dest');
 assert.equal(component(render(),'./FolderPickerDialog').props.open,false);await tick();assert.deepEqual(calls,[{ids:[1,2],destination:'/dest'}]);
 d.resolve({moved_ids:[1,2],skipped:[]});await tick();assert.deepEqual([...selection.selectedIds],[3]);
});
test('bulk delete handles a backend error also listed as skipped without overlapping receipt members',async()=>{
 const e=environment(async()=>response({removed:1,processed_ids:[1],skipped_ids:[2],errors:[{id:2,reason:'read-only'}]}));
 await e.api.bulkDeleteMedia([1,2],'DELETE_FILES');
 const record=e.core.optimisticOperations.getSnapshot()[0];assert.equal(record.state,'partially_committed');assert.equal(record.receipt.failed.length,1);assert.equal(record.receipt.skipped.length,0);
});
test('late delete completion does not clear a newer page-owned selection',async()=>{
 const runtime=hooks(),d=deferred();let hotkey,clears=0,ids=[1];
 const Dialogs=load('hotkeys/SelectionHotkeyDialogs.tsx',runtime,{
  './useHotkey':{useHotkeys:(_keys,handler)=>{hotkey=handler}},'./keymap':{selectionBindings:[{key:'Delete'}]},
  '../services/mediaActions':{bulkDeleteMedia:()=>d.promise},'../components/BulkResolveToolbar':{resolveConfirmCopy:()=>({})},
 }).SelectionHotkeyDialogs;
 const render=()=>runtime.render(()=>Dialogs({mediaIds:ids,includeDelete:true,onProcessed:()=>{clears++}}));render();hotkey({key:'Delete'});
 const done=component(render(),'../components/ConfirmDialog').props.onConfirm();ids=[2];render();
 d.resolve({removed:1,processed_ids:[1],skipped_ids:[],errors:[]});await done;assert.equal(clears,0);
});
