const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
function environment() {
  const cache = new Map();
  function load(relative) {
    const file = path.resolve(__dirname, '../src', relative);
    if (cache.has(file)) return cache.get(file).exports;
    const module = { exports: {} }; cache.set(file, module);
    const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } }).outputText;
    new Function('require', 'module', 'exports', code)(id => {
      if (id === '../config') return { API: '' };
      return id.startsWith('.') ? load(path.relative(path.resolve(__dirname, '../src'), path.resolve(path.dirname(file), id + '.ts'))) : require(id);
    }, module, module.exports);
    return module.exports;
  }
  return { ...load('services/review.ts'), ...load('stores/optimisticOperations.ts'), bus: load('stores/mutationBus.ts').mutationBus };
}
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b; }); return {promise,resolve,reject}; };
const tick = () => new Promise(r => setImmediate(r));
test('explicit review selection disappears before transport, then restores skipped and failed IDs', async () => {
  const e=environment(), d=deferred(), events=[];
  e.bus.subscribe(event => events.push(event));
  const h=e.startReviewResolve({label:'Review', ids:[1,2,3], request:()=>d.promise});
  assert.deepEqual(e.projectList('blur','media',[{id:1},{id:2},{id:3}],3).items, []);
  d.resolve({removed:1, processed_ids:[1], skipped_ids:[2,3], errors:[{id:3,reason:'Read only'}]});
  assert.equal((await h.done).state,'partially_committed');
  assert.deepEqual(e.projectList('blur','media',[{id:1},{id:2},{id:3}],3), {items:[{id:2},{id:3}],total:2});
  assert.match(e.optimisticOperations.getSnapshot()[0].error,/Read only/);
  assert.deepEqual(events,[{type:'media:deleted',ids:[1]}]);
});
test('count-only results never manufacture processed IDs and restore optimistic rows for authoritative refresh',async()=>{
 const e=environment(),d=deferred();let refresh=0;
 const h=e.startReviewResolve({label:'Missing',ids:[1,2],query:'missing:false:photos',countOnly:true,request:()=>d.promise,refresh:async()=>{refresh++}});
 assert.equal(e.projectList('missing:false:photos','media',[{id:1},{id:2}],2).items.length,0);
 assert.equal(e.projectList('images','media',[{id:1}],1).items.length,1);
 d.resolve({deleted:1});const r=await h.done;await tick();
 assert.deepEqual(r.receipt.applied,[]);assert.deepEqual(r.patches,[]);assert.equal(refresh,1);
});
test('filtered select-all has pending feedback without pretending loaded rows are the server snapshot',async()=>{
 const e=environment(),d=deferred();const h=e.startReviewResolve({label:'All matching',ids:[],request:()=>d.promise});
 assert.equal(e.optimisticOperations.getSnapshot()[0].label,'All matching');
 assert.equal(e.projectList('blur','media',[{id:1}],20).total,20);
 d.resolve({removed:1,processed_ids:[8],skipped_ids:[]});assert.deepEqual((await h.done).receipt.applied,['media:8']);
});
test('lost destructive response stays uncertain, is not retried, and global error survives route exit',async()=>{
 const e=environment();let calls=0;
 const h=e.startReviewResolve({label:'Delete',ids:[1],request:async()=>{calls++;throw Error('Lost response')}});
 assert.equal((await h.done).state,'unknown');assert.equal(calls,1);
 assert.equal(e.projectList('blur','media',[{id:1}],1).items.length,0);
 assert.match(e.optimisticOperations.getSnapshot()[0].error,/Lost response/);
});
test('unaccounted explicit members stay uncertain instead of being reported as completed',async()=>{
 const e=environment();const h=e.startReviewResolve({label:'Delete',ids:[1,2],request:async()=>({removed:1,processed_ids:[1]})});
 const r=await h.done;assert.equal(r.state,'unknown');assert.deepEqual(r.receipt.unknown,['media:2']);
});
function toolbar(relative = "components/BulkResolveToolbar.tsx", overrides = {}) {
 let cursor=0;const state=[];const jsx=(type,props)=>({type,props:props||{}});
 const component=name=>Object.assign(()=>null,{displayName:name});
 const react={useState:initial=>{const i=cursor++;if(!(i in state))state[i]=initial;return [state[i],v=>state[i]=typeof v==='function'?v(state[i]):v]},useLayoutEffect:()=>{},useEffect:()=>{},useRef:initial=>({current:initial}),useCallback:fn=>fn,createElement:(type,props,...children)=>jsx(type,{...props,children})};
 const commands=[];const stubs={react:{__esModule:true,default:react,...react},'react/jsx-runtime':{jsx,jsxs:jsx},'../services/review':{startReviewResolve:command=>{commands.push(command);return {done:Promise.resolve()}}},'../hotkeys/useHotkey':{useHotkey:()=>{},useHotkeys:()=>{}},'../hotkeys/keymap':{selectionBindings:[]},'../config':{__esModule:true,default:{PRESENTATION_MODE:false}},'../formatUtils':{formatBytes:String},...overrides};
 const module={exports:{}};const file=path.resolve(__dirname,'../src', relative);
 const code=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText;
 new Function('require','module','exports',code)(id=>stubs[id]||(id==='@mui/material'?new Proxy({},{get:(_,key)=>component(key)}):{__esModule:true,default:component(path.basename(id))}),module,module.exports);
 return {commands,render:props=>{cursor=0;return module.exports.default(props)}};
}
function find(tree,name,predicate=()=>true){if(!tree||typeof tree!=='object')return; if((tree.type?.displayName||tree.type)===name&&predicate(tree.props))return tree;for(const child of [tree.props?.children].flat(Infinity)){const found=find(child,name,predicate);if(found)return found}}
test('toolbar captures IDs, count and filtered resolver when opening confirmation and dismisses without waiting',async()=>{
 const h=toolbar(),calls=[],d=deferred();const props={statsText:'Review',shownCount:2,shownSize:2,total:20,selectedIds:new Set([1,2]),onSelectVisible(){},onClearSelection(){},resolve:p=>{calls.push(['old',p]);return d.promise},onResolved(){},onFeedback(){}};
 find(h.render(props),'Button',p=>p.children==='Remove records').props.onClick();
 const changed={...props,selectedIds:new Set([9]),total:99,resolve:p=>{calls.push(['new',p]);return d.promise}};
 const confirm=find(h.render(changed),'ConfirmDialog');assert.match(confirm.props.message,/2 record/);
 confirm.props.onConfirm();assert.equal(find(h.render(changed),'ConfirmDialog').props.open,false);
 assert.equal(h.commands.length,1);h.commands[0].request();d.resolve({removed:0,processed_ids:[]});
 assert.deepEqual(calls,[['old',{action:'DELETE_RECORDS',mediaIds:[1,2]}]]);
});

for (const missing of [false,true]) test(`${missing?'missing':'broken'} table snapshots selected IDs before confirmation`,()=>{
 const list={items:[{id:1},{id:2}],total:2}, selection={selectedIds:new Set([1,2]),clear(){},deselect(){},selectAll(){}};
 const calls=[];
 const h=toolbar(`pages/${missing?'MissingFilesPage':'BrokenMediaPage'}.tsx`,{
  '../hooks/usePagedList':{usePagedList:()=>list},
  '../hooks/useIdSelection':{useIdSelection:()=>selection},
  '../stores/useListStore':{useOptimisticCachedList:()=>list,refreshCachedList:async()=>{}},
  '../TaskEventsContext':{useTaskEvents:()=>({activeTasks:[]}),useTaskCompletionVersion:()=>0},
  '../services/missing':{confirmMissing:async payload=>{calls.push(payload);return {deleted:1}}},
  '../services/broken':{resolveBroken:async payload=>{calls.push(payload);return {removed:1,processed_ids:[1]}}},
  '../urlUtils':{encodeFilePath:String},
  '../components/BulkResolveToolbar':{resolveConfirmCopy:(action,count)=>({message:`${count} record(s)`})},
 });
 find(h.render(), 'Button',p=>p.children===(missing?'Confirm selected':'Remove records')).props.onClick();
 selection.selectedIds=new Set([9]);
 const dialog=find(h.render(),'ConfirmDialog');assert.match(dialog.props.message,/2 record/);
 dialog.props.onConfirm();assert.equal(find(h.render(),'ConfirmDialog').props.open,false);
 assert.equal(h.commands.length,1);h.commands[0].request();assert.deepEqual(calls[0].media_ids,[1,2]);
});
test('definitive denial restores only that operation while refresh failure keeps successful deletion',async()=>{
 const e=environment(),d=deferred();
 const one=e.startReviewResolve({label:'Denied',ids:[1],request:()=>d.promise});
 const two=e.startReviewResolve({label:'Saved',ids:[2],request:async()=>({removed:1,processed_ids:[2]}),refresh:async()=>{throw Error('Refresh offline')}});
 d.reject(new e.DefinitiveMutationError('Forbidden'));assert.equal((await one.done).state,'rejected');
 assert.equal((await two.done).state,'committed');await tick();
 assert.deepEqual(e.projectList('blur','media',[{id:1},{id:2}],2).items,[{id:1}]);
 assert.match(e.optimisticOperations.getSnapshot().find(r=>r.id===two.id).refreshError,/Refresh offline/);
});
test('all matching confirmation retains its original filter closure and count',()=>{
 const h=toolbar(),calls=[];const props={statsText:'Review',shownCount:1,shownSize:1,total:20,selectedIds:new Set(),onSelectVisible(){},onClearSelection(){},resolve:p=>{calls.push(['photos',p]);return Promise.resolve({removed:0,processed_ids:[]})},onResolved(){},onFeedback(){}};
 find(h.render(props),'MenuItem',p=>p.children[0]==='Remove all ').props.onClick();
 const changed={...props,total:99,resolve:p=>{calls.push(['videos',p]);return Promise.resolve({removed:0,processed_ids:[]})}};
 const dialog=find(h.render(changed),'ConfirmDialog');assert.match(dialog.props.message,/20 record/);dialog.props.onConfirm();
 assert.deepEqual(h.commands[0].ids,[]);h.commands[0].request();assert.deepEqual(calls,[['photos',{action:'DELETE_RECORDS',selectAll:true}]]);
});
