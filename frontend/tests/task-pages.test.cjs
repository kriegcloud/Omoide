// Real TS/TSX, controlled hooks and deferred network requests; no DOM runner needed.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const React = require('react');
const noop = () => {};
const tick = async () => { for (let i=0; i<12; i++) await Promise.resolve(); };
const deferred = () => { let resolve, reject; const promise=new Promise((a,b)=>{resolve=a;reject=b;}); return {promise,resolve,reject}; };
function harness() {
  const slots=[]; let cursor=0, dirty=false, effects=[];
  const equal=(a,b)=>a&&b&&a.length===b.length&&a.every((v,i)=>Object.is(v,b[i]));
  const api={...React,
    useRef(value){return slots[cursor++]??={current:value};},
    useState(value){const i=cursor++; if(!(i in slots))slots[i]=typeof value==='function'?value():value; return [slots[i],next=>{const value=typeof next==='function'?next(slots[i]):next;if(!Object.is(value,slots[i])){slots[i]=value;dirty=true;}}];},
    useMemo(fn,deps){const i=cursor++;if(!slots[i]||!equal(slots[i].deps,deps))slots[i]={deps,value:fn()};return slots[i].value;},
    useCallback(fn,deps){return api.useMemo(()=>fn,deps);},
    useEffect(fn,deps){const i=cursor++;if(!slots[i]||!equal(slots[i].deps,deps))effects.push(()=>{slots[i]?.cleanup?.();slots[i]={deps,cleanup:fn()};});},
  };
  return {api,render(fn){for(let n=0;n<20;n++){cursor=0;dirty=false;effects=[];const tree=fn();effects.forEach(f=>f());if(!dirty)return tree;}throw Error('render loop');},unmount(){slots.forEach(s=>s?.cleanup?.());}};
}
function load(file,h,overrides={}) {
  const ui=new Proxy({useTheme:()=>({palette:{background:{default:'white'}}})},{get:(v,k)=>v[k]??k});
  const list={lists:{},fetchInitial:noop,loadMore:noop,removeItem:noop,clearList:noop,clearListsByPrefix:noop};
  const modules={react:h.api,'@mui/material':ui,'react-masonry-css':'Masonry','react-intersection-observer':{useInView:()=>({ref:noop,inView:false})},
    'react-router-dom':{useSearchParams:()=>[new URLSearchParams('view=folders&folder=a'),noop],useLocation:()=>({key:'route',state:null}),useNavigate:()=>noop},
    '../stores/useListStore':{defaultListState:{items:[],hasMore:false,isLoading:false,error:null},useListInvalidation:noop,useListStore:(selector)=>selector?selector(list):list,refreshCachedList:async()=>{}},
    '../context/SelectionContext':{useSelection:()=>({selectedIds:new Set(),beginSelecting:noop,clear:noop,setSelected:noop})},
    '../context/UndoContext':{useUndoRefresh:noop},'../hooks/useMarqueeSelection':{useGridSelection:()=>({})},
    '../hotkeys/useHotkey':{useHotkeys:noop},'../TaskEventsContext':{useTaskCompletionVersion:()=>0},'../hooks/useHomeWidgets':{useHomeWidgets:()=>({widgets:[{id:'recent_media',enabled:true}]})},
    '../config':{API:''},'../services/media':{getMediaList:async()=>({items:[],next_cursor:null}),getMediaFolders:async()=>({folders:[],breadcrumbs:[]})},
    '../services/features':{getCameras:async()=>[]},'react-leaflet':{MapContainer:'MapContainer',TileLayer:'TileLayer',Marker:'Marker',Popup:'Popup'},
    '../utils/leaflet':{},'../urlUtils':{encodeFilePath:encodeURIComponent},'../stores/mutationBus':{mutationBus:{subscribe:()=>noop}},
    ...overrides};
  const src=fs.readFileSync(path.resolve(__dirname,'../src',file),'utf8').replaceAll('import.meta.env.DEV','false');
  const code=ts.transpileModule(src,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText;
  const mod={exports:{}};
  new Function('require','module','exports',code)(id=>{if(id in modules)return modules[id];if(id.startsWith('../components/')||id.startsWith('@mui/icons-material/'))return new Proxy({__esModule:true,default:id},{get:(v,k)=>v[k]??k}); if(id.startsWith('../hooks/')||id.startsWith('../stores/'))return load(id.slice(3)+'.ts',h,overrides); return require(id);},mod,mod.exports);
  return mod.exports;
}
function find(node,predicate){if(!node||typeof node!=='object')return; if(predicate(node))return node; for(const child of [node.props?.children].flat(Infinity)){const match=find(child,predicate);if(match)return match;}}
function contexts(tree){const result=[];function visit(n){if(!n||typeof n!=='object')return;if(n.props?.value)result.push(n.props.value);[n.props?.children].flat(Infinity).forEach(visit);}visit(tree);return result;}
function env(){global.window={addEventListener:noop,removeEventListener:noop,setTimeout,clearTimeout,innerHeight:800,innerWidth:1200,scrollY:0};global.document={visibilityState:'visible',addEventListener:noop,removeEventListener:noop};}
function task(id,status='running'){return {id,status,task_type:'run_processor_for_media'};}
function provider(overrides){env();const h=harness();const {TaskEventsProvider}=load('TaskEventsContext.tsx',h,overrides);const render=()=>h.render(()=>TaskEventsProvider({children:'children'}));const value=()=>contexts(render()).find(v=>v.forceRefresh&&v.activeTasks);return {h,render,value};}

test('task completion survives a failed detail lookup when a terminal recent task is available',async()=>{
  let active=[task('1')],recent=[];let details=0;
  const p=provider({'./services/taskActions':{getActiveTasks:async()=>active,getRecentTasks:async()=>recent},'./services/task':{getTask:async()=>{details++;throw Error('offline');}}});
  await p.value().forceRefresh();active=[];recent=[task('1','completed')];await p.value().forceRefresh();
  assert.equal(p.value().completionCounters.run_processor_for_media,1);assert.equal(details,0);p.h.unmount();
});
test('disappeared task with failed detail fetch remains pending and retries once',async()=>{
  let active=[task('1')],attempts=0;
  const p=provider({'./services/taskActions':{getActiveTasks:async()=>active,getRecentTasks:async()=>[]},'./services/task':{getTask:async()=>{if(++attempts===1)throw Error('offline');return task('1','completed');}}});
  await p.value().forceRefresh();active=[];await p.value().forceRefresh();await p.value().forceRefresh();await p.value().forceRefresh();
  assert.equal(p.value().completionCounters.run_processor_for_media,1);assert.equal(attempts,2);p.h.unmount();
});
test('concurrent task refresh callers share the single request',async()=>{
  const first=deferred();let calls=0;
  const p=provider({'./services/taskActions':{getActiveTasks:()=>{calls++;return first.promise;},getRecentTasks:async()=>[]},'./services/task':{getTask:async()=>null}});
  const v=p.value();const a=v.forceRefresh(),b=v.forceRefresh(),c=v.forceRefresh();first.resolve([]);await Promise.all([a,b,c]);assert.equal(calls,1);p.h.unmount();
});
test('completion context keeps its reference across unchanged active task polls',async()=>{
  const p=provider({'./services/taskActions':{getActiveTasks:async()=>[],getRecentTasks:async()=>[]},'./services/task':{getTask:async()=>null}});
  const stable=()=>contexts(p.render()).find(v=>v.completionCounters&&!('activeTasks'in v));
  const before=stable();assert.ok(before);await p.value().forceRefresh();assert.equal(stable(),before);p.h.unmount();
});
test('Highlights ignores a response from the previously selected year',async()=>{
  env();const h=harness(),first=deferred(),second=deferred();const {default:Page}=load('pages/HighlightsPage.tsx',h,{'../services/features':{getHighlightYears:async()=>[{year:2025},{year:2024}],getHighlights:year=>year===2025?first.promise:second.promise}});
  const render=()=>h.render(Page);render();await tick();let tree=render();find(tree,n=>n.type==='Chip'&&n.props.label===2024).props.onClick();render();second.resolve([{id:24}]);await tick();render();first.resolve([{id:25}]);await tick();tree=render();assert.equal(find(tree,n=>n.props?.media)?.props.media.id,24);h.unmount();
});
test('Index folder request cannot replace a newer folder listing',async()=>{
  env();const h=harness(),first=deferred(),second=deferred();let folder='a';const {default:Page}=load('pages/IndexPage.tsx',h,{'react-router-dom':{useSearchParams:()=>[new URLSearchParams(`view=folders&folder=${folder}`),noop],useLocation:()=>({key:folder,state:null}),useNavigate:()=>noop},'../services/media':{getMediaList:async()=>({items:[]}),getMediaFolders:f=>f==='a'?first.promise:second.promise}});
  const render=()=>h.render(Page);render();folder='b';render();second.resolve({folders:[{path:'b',name:'B'}],breadcrumbs:[]});await tick();render();first.resolve({folders:[{path:'a',name:'A'}],breadcrumbs:[]});await tick();const tree=render();assert.equal(find(tree,n=>n.props?.folder)?.props.folder.path,'b');h.unmount();
});
test('Search suspends pagination after failure while sentinel stays visible',async()=>{
  env();const h=harness();let calls=0;const {default:Page}=load('pages/SearchResultPage.tsx',h,{'react-router-dom':{useSearchParams:()=>[new URLSearchParams('query=test'),noop],useLocation:()=>({key:'search',state:null})},'react-intersection-observer':{useInView:()=>({ref:noop,inView:true})},'../services/search':{searchCombined:async()=>{calls++;if(calls===1)return {media:[{id:1,thumbnail_path:'x'}],next_cursor:'next'};throw Error('offline');}}});
  const render=()=>h.render(Page);render();await tick();render();await tick();render();await tick();const tree=render();assert.equal(calls,2);assert.ok(find(tree,n=>n.type==='Alert'&&n.props.severity==='error'));h.unmount();
});
test('Map geocoder ignores an old response when query was shortened',async()=>{
  env();const timers=[];const original=global.setTimeout,originalClear=global.clearTimeout;global.setTimeout=fn=>{timers.push(fn);return timers.length;};global.clearTimeout=noop;const first=deferred();global.fetch=()=>first.promise;
  try{const h=harness();const {default:Page}=load('pages/MapEditorPage.tsx',h,{'../services/mapEditor':{getMissingGeoMedia:async()=>({items:[]})}});const render=()=>h.render(Page);let tree=render();find(tree,n=>n.type==='Autocomplete').props.onInputChange(null,'Paris');render();timers.shift()();tree=render();find(tree,n=>n.type==='Autocomplete').props.onInputChange(null,'Pa');render();first.resolve({ok:true,json:async()=>[{display_name:'old'}]});await tick();tree=render();assert.deepEqual(find(tree,n=>n.type==='Autocomplete').props.options,[]);h.unmount();}finally{global.setTimeout=original;global.clearTimeout=originalClear;delete global.fetch;}
});

test('Highlights removes a deleted card while an old refresh is still pending', async () => {
  env();const h=harness(),refresh=deferred();let listener,refreshPage,calls=0;
  const {default:Page}=load('pages/HighlightsPage.tsx',h,{'../services/features':{getHighlightYears:async()=>[{year:2025}],getHighlights:()=>++calls===1?Promise.resolve([{id:1},{id:2}]):refresh.promise},'../context/UndoContext':{useUndoRefresh:(key,fn)=>{refreshPage=fn;}},'../stores/mutationBus':{mutationBus:{subscribe:fn=>{listener=fn;return noop;}}}});
  const render=()=>h.render(Page);render();await tick();render();await tick();render();const pending=refreshPage();listener?.({type:'media:deleted',ids:[1]});refresh.resolve([{id:1},{id:2}]);await pending;const tree=render();assert.equal(find(tree,n=>n.props?.media)?.props.media.id,2);h.unmount();
});
test('Search image results receive card mutation events', async () => {
  env();const h=harness();let listener;const location={key:'image',state:{searchType:'image',items:[{id:1,thumbnail_path:'1'},{id:2,thumbnail_path:'2'}]}};
  const {default:Page}=load('pages/SearchResultPage.tsx',h,{'react-router-dom':{useSearchParams:()=>[new URLSearchParams(),noop],useLocation:()=>location},'../services/search':{},'../stores/mutationBus':{mutationBus:{subscribe:fn=>{listener=fn;return noop;}}}});
  const render=()=>h.render(Page);render();listener?.({type:'media:deleted',ids:[1]});let tree=render();assert.equal(find(tree,n=>n.props?.media)?.props.media.id,2);listener({type:'media:updated',items:[{id:2,is_favorite:true}]});tree=render();assert.equal(find(tree,n=>n.props?.media)?.props.media.is_favorite,true);h.unmount();
});

test('task snapshot resolving after provider unmount is ignored', async () => {
  const pending=deferred();const p=provider({'./services/taskActions':{getActiveTasks:()=>pending.promise,getRecentTasks:async()=>[]},'./services/task':{getTask:async()=>null}});
  const refresh=p.value().forceRefresh();p.h.unmount();pending.resolve([task('old')]);await refresh;assert.deepEqual(p.value().activeTasks,[]);
});
test('windowed layout bounds the viewport for ten thousand items', () => {
  const h=harness();const {windowedGridLayout,nextWindowedIndex}=load('hooks/useWindowedGrid.ts',h);
  const layout=windowedGridLayout(1200,10000,900);assert.equal(layout.columns,5);assert.equal(layout.rows,2000);assert.equal(layout.height,680);
  assert.equal(nextWindowedIndex(9,'ArrowDown',5,12),11);assert.equal(nextWindowedIndex(11,'ArrowDown',5,12),11);assert.equal(nextWindowedIndex(0,'ArrowUp',5,12),0);
  const {FixedSizeGrid}=require('react-window');let cells=0;
  require('react-dom/server').renderToStaticMarkup(React.createElement(FixedSizeGrid,{width:1200,height:layout.height,columnCount:layout.columns,columnWidth:layout.cellSize,rowCount:layout.rows,rowHeight:layout.cellSize,overscanRowCount:2},()=>{cells++;return React.createElement('div');}));
  assert.ok(cells<=30,`expected bounded cells, saw ${cells}`);
});
test('windowed keyboard focus scrolls to a currently unmounted row', () => {
  env();const h=harness();let registration,scrolled,focused;
  const {useWindowedGrid}=load('hooks/useWindowedGrid.ts',h,{'../hotkeys/useHotkey':{useHotkeys:(bindings,handler,options)=>{registration={handler,options};}}});
  const nodes=[{dataset:{selectableId:'8'},focus:()=>{focused=8;}}];
  const container={contains:()=>true,querySelectorAll:()=>nodes,querySelector:()=>nodes[0]};
  const grid=h.render(()=>useWindowedGrid({ids:[0,1,2,3,4,5,6,7,8,9],containerRef:{current:container},loadMore:noop,hasMore:false,loading:false,error:null,listKey:'test'}));
  grid.gridRef.current={scrollToItem:value=>{scrolled=value;}};
  const originalRAF=global.requestAnimationFrame, originalCancel=global.cancelAnimationFrame;
  global.requestAnimationFrame=fn=>{fn();return 1;};global.cancelAnimationFrame=noop;
  try { registration.handler({target:{closest:()=>({dataset:{selectableId:'4'}})},key:'ArrowDown'}); assert.equal(scrolled.rowIndex,2);assert.equal(focused,8);h.unmount(); }
  finally{global.requestAnimationFrame=originalRAF;global.cancelAnimationFrame=originalCancel;}
});

test('stopped polling cycle cannot schedule another timer after restart', async () => {
  const pending=deferred();const p=provider({'./services/taskActions':{getActiveTasks:()=>pending.promise,getRecentTasks:async()=>[]},'./services/task':{getTask:async()=>null}});
  const timers=[];window.setTimeout=fn=>{timers.push(fn);return timers.length;};window.clearTimeout=noop;
  const unsubscribe=p.value().subscribe();unsubscribe();const finish=p.value().subscribe();pending.resolve([]);await tick();assert.equal(timers.length,1);finish();p.h.unmount();
});

test('Search Retry waits briefly and resumes the failed cursor without discarding loaded items', async () => {
  env();const h=harness();const cursors=[];const {default:Page}=load('pages/SearchResultPage.tsx',h,{'react-router-dom':{useSearchParams:()=>[new URLSearchParams('query=test'),noop],useLocation:()=>({key:'search',state:null})},'react-intersection-observer':{useInView:()=>({ref:noop,inView:true})},'../services/search':{searchCombined:async(q,n,cursor)=>{cursors.push(cursor);if(cursors.length===1)return {media:[{id:1,thumbnail_path:'1'}],next_cursor:'next'};if(cursors.length===2)throw Error('offline');return {media:[{id:2,thumbnail_path:'2'}],next_cursor:null};}}});
  const render=()=>h.render(Page);render();await tick();render();await tick();const tree=render();let retry;const original=global.setTimeout;global.setTimeout=(fn,delay)=>{assert.equal(delay,750);retry=fn;return 100;};
  try{find(tree,n=>n.type==='Alert'&&n.props.severity==='error').props.action.props.onClick();assert.equal(cursors.length,2);retry();await tick();const done=render();assert.deepEqual(cursors,[undefined,'next','next']);assert.ok(find(done,n=>n.props?.media?.id===1));assert.ok(find(done,n=>n.props?.media?.id===2));h.unmount();}finally{global.setTimeout=original;}
});

test('task polling pauses while hidden and resumes on visibility change', async () => {
  let calls=0;const p=provider({'./services/taskActions':{getActiveTasks:async()=>{calls++;return [];},getRecentTasks:async()=>[]},'./services/task':{getTask:async()=>null}});
  let visibility,timer;document.visibilityState='hidden';document.addEventListener=(name,fn)=>{if(name==='visibilitychange')visibility=fn;};window.setTimeout=fn=>{timer=fn;return 1;};window.clearTimeout=noop;
  const unsubscribe=p.value().subscribe();await tick();assert.equal(calls,0);document.visibilityState='visible';visibility();await tick();assert.equal(calls,1);document.visibilityState='hidden';timer();await tick();assert.equal(calls,1);unsubscribe();p.h.unmount();
});

test('Index consumes album selection intent once after route synchronization', () => {
  env();const h=harness();let selections=0;const location={key:'album-add',state:{beginSelection:true,addToAlbumId:42}};
  const {default:Page}=load('pages/IndexPage.tsx',h,{'react-router-dom':{useSearchParams:()=>[new URLSearchParams(),noop],useLocation:()=>location},'../context/SelectionContext':{useSelection:()=>({selectedIds:new Set(),beginSelecting:()=>{selections++;},clear:noop,setSelected:noop})}});
  h.render(Page);h.render(Page);assert.equal(selections,1);h.unmount();
});

function mutationChannel() {
  const listeners=new Set();return {subscribe(fn){listeners.add(fn);return()=>listeners.delete(fn);},emit(event){[...listeners].forEach(fn=>fn(event));}};
}
for (const category of ['tag','scene']) test(`Search ${category} cache refetches after matching invalidation`, async () => {
  env();const h=harness(),bus=mutationChannel(),fetchers=new Map();let calls=0;
  const state={lists:{},fetchInitial:(key,fetcher)=>{fetchers.set(key,fetcher);return fetcher();},loadMore:noop,removeItem:noop};
  const {default:Page}=load('pages/SearchResultPage.tsx',h,{'react-router-dom':{useSearchParams:()=>[new URLSearchParams(`category=${category}&query=test`),noop],useLocation:()=>({key:category,state:null})},'../stores/mutationBus':{mutationBus:bus},'../services/search':{searchTags:async()=>{calls++;return {items:[],next_cursor:null};},searchScenes:async()=>{calls++;return {items:[],next_cursor:null};}},'../stores/useListStore':{defaultListState:{items:[],hasMore:false,isLoading:false,error:null},useListStore:selector=>selector(state),refreshCachedList:async()=>{},useListInvalidation:key=>h.api.useEffect(()=>bus.subscribe(event=>{if(event.type==='list:invalidate'&&key.startsWith(event.prefix))void fetchers.get(key)?.();}),[key])}});
  h.render(Page);await tick();assert.equal(calls,1);bus.emit({type:'list:invalidate',prefix:'/api/search'});await tick();assert.equal(calls,2);h.unmount();
});
test('Search local combined results refresh on global invalidation', async () => {
  env();const h=harness(),bus=mutationChannel();let calls=0;
  const {default:Page}=load('pages/SearchResultPage.tsx',h,{'react-router-dom':{useSearchParams:()=>[new URLSearchParams('query=test'),noop],useLocation:()=>({key:'search',state:null})},'../stores/mutationBus':{mutationBus:bus},'../services/search':{searchCombined:async()=>({media:[{id:++calls,thumbnail_path:'x'}],next_cursor:null})}});
  const render=()=>h.render(Page);render();await tick();render();bus.emit({type:'list:invalidate',prefix:'orphan-faces'});await tick();assert.equal(calls,1);bus.emit({type:'list:invalidate',prefix:''});await tick();const tree=render();assert.equal(calls,2);assert.equal(find(tree,n=>n.props?.media)?.props.media.id,2);h.unmount();
});
test('Highlights local results refresh on global invalidation', async () => {
  env();const h=harness(),bus=mutationChannel();let calls=0;
  const {default:Page}=load('pages/HighlightsPage.tsx',h,{'../stores/mutationBus':{mutationBus:bus},'../services/features':{getHighlightYears:async()=>[{year:2025}],getHighlights:async()=>[{id:++calls}]}});
  const render=()=>h.render(Page);render();await tick();render();await tick();render();bus.emit({type:'list:invalidate',prefix:'orphan-faces'});await tick();assert.equal(calls,1);bus.emit({type:'list:invalidate',prefix:''});await tick();const tree=render();assert.equal(calls,2);assert.equal(find(tree,n=>n.props?.media)?.props.media.id,2);h.unmount();
});
test('image-search invalidation removes missing media and refreshes surviving previews', async () => {
  env();const h=harness(),bus=mutationChannel();const location={key:'image',state:{searchType:'image',items:[{id:1,thumbnail_path:'1'},{id:2,thumbnail_path:'2'}]}};
  const {default:Page}=load('pages/SearchResultPage.tsx',h,{'react-router-dom':{useSearchParams:()=>[new URLSearchParams(),noop],useLocation:()=>location},'../stores/mutationBus':{mutationBus:bus},'../services/search':{},'../services/media':{getMedia:async id=>{if(id==='1')throw Object.assign(Error('missing'),{status:404});return {media:{id:2,thumbnail_path:'fresh'}};}}});
  const render=()=>h.render(Page);render();bus.emit({type:'list:invalidate',prefix:''});await tick();const tree=render();assert.equal(find(tree,n=>n.props?.media)?.props.media.id,2);assert.equal(find(tree,n=>n.props?.media)?.props.media.thumbnail_path,'fresh');h.unmount();
});
