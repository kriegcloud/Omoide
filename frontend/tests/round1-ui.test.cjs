const assert=require('node:assert/strict');
const {test}=require('node:test');
const {hooks,load,find,component,deferred,settle,listStore,noop}=require('./detailRuntime.cjs');
const text=n=>Array.isArray(n)?n.map(text).join(''):n&&typeof n==='object'?text(n.props?.children):String(n??'');
function entityPage(kind,services={}) {
 const runtime=hooks(),route={id:'1'},first=deferred(),second=deferred(),calls=[];
 const Page=load(`pages/${kind}DetailPage.tsx`,runtime,{
  'react-router-dom':{useParams:()=>route,useNavigate:()=>(...args)=>calls.push(args)},
  '../config':{__esModule:true,default:{PRESENTATION_MODE:false,EVENTS_ENABLED:true}},
  '../context/SelectionContext':{useSelection:()=>({selectedIds:new Set(),clear:noop,isSelecting:false})},
  '../services/features':{['get'+kind]:id=>id===1?first.promise:second.promise,...services},
 }).default;
 return {runtime,route,first,second,calls,render:()=>runtime.render(Page)};
}
for(const kind of ['Album','Event'])test(`${kind} detail rejects old responses after navigation even when abort is ignored`,async()=>{
 const p=entityPage(kind);p.render();p.route.id='2';p.render();
 const row=id=>({id,name:'album-'+id,title:'event-'+id,media_count:1,start_at:'2026-01-01',end_at:'2026-01-01'});
 p.second.resolve(row(2));await settle();p.render();p.first.resolve(row(1));await settle();
 const title=find(p.render(),n=>n.type==='Typography'&&n.props.variant==='h5');
 assert.match(text(title),/-2$/);p.runtime.unmount();
});
test('album delete confirmation never retargets to a newly navigated album',async()=>{
 const deleted=[];const p=entityPage('Album',{deleteAlbum:async id=>deleted.push(id)});p.render();p.first.resolve({id:1,name:'one',media_count:1});await settle();
 const tooltip=find(p.render(),n=>n.type==='Tooltip'&&n.props.title==='Delete album (keeps the media)');tooltip.props.children.props.onClick();
 p.route.id='2';p.render();p.second.resolve({id:2,name:'two',media_count:1});await settle();
 const dialog=component(p.render(),'../components/ConfirmDialog');
 if(dialog?.props.open)await dialog.props.onConfirm();
 else {const button=find(p.render(),n=>n.type==='Button'&&text(n)==='Delete');if(button)await button.props.onClick();}
 assert.ok(!deleted.includes(2));p.runtime.unmount();
});
test('person delete confirmation does not survive navigation to another person',async()=>{
 const runtime=hooks(),route={id:'1',state:{}},store=listStore(),deleted=[];
 global.window={addEventListener:noop,removeEventListener:noop};
 const hook=load('hooks/usePersonDetailPage.ts',runtime,{
  'react-router-dom':{useParams:()=>route,useLocation:()=>route,useNavigate:noop},
  '../services/person':{getPerson:async id=>({id:Number(id),name:'person-'+id})},
  '../services/personActions':{getSuggestedFaces:async()=>[],getPersonFaces:async()=>({items:[]}),deletePerson:async id=>deleted.push(id)},
  '../stores/useListStore':store.module,
 }).usePersonDetailPage;
 const render=()=>runtime.render(hook);render();await settle();render().setConfirmDelete(true);route.id='2';render();await settle();
 const next=render();assert.equal(next.confirmDelete,false);await next.handleDeletePerson();assert.deepEqual(deleted,[]);runtime.unmount();
});
test('people detail patches invalidate server-sorted and filtered variants',()=>{
 let state={lists:{}};for(const suffix of ['name_asc-','appearances_asc-','favorites-','name_asc-Amy'])state.lists['people-grid-all-'+suffix]={items:[{id:1,name:'Amy',appearance_count:1},{id:2,name:'Zoe',appearance_count:100}]};
 const store={getState:()=>state,setState:fn=>{state={...state,...fn(state)};}};state.clearList=key=>delete state.lists[key];
 const cache=load('stores/peopleCache.ts',hooks(),{'./useListStore':{useListStore:store,readCachedList:(state,key)=>state.lists[key]}});
 cache.patchPersonInGrids({id:1,name:'Renamed',appearance_count:2});assert.deepEqual(Object.keys(state.lists),[]);
});
for(const [result,expected,severity] of [[{exported:0,skipped:1000,failure_count:1000},'Exported 0 media files; skipped 1000.','error'],[null,'Media export completed; outcome counts are unavailable.','warning']])test('person export uses authoritative totals or reports unknown outcomes '+severity,async()=>{
 const timers=[],messages=[];global.window={setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout:noop};const runtime=hooks();
 const Page=load('pages/PersonDetailPage.tsx',runtime,{
  '../hooks/usePersonDetailPage':{usePersonDetailPage:()=>({person:{id:7,appearance_count:1000},loading:false,snackbar:{open:false},setSnackbar:value=>messages.push(value)})},
  '../services/config':{pickDirectory:async()=>'/fixture'},'../services/personActions':{exportPersonMedia:async()=>({id:'export',status:'running'})},
  '../services/task':{getTask:async()=>({id:'export',status:'completed',processed:1000,failure_count:1000,result})},
  '../services/taskActions':{getTaskFailures:async()=>Array.from({length:500},()=>({path:'fixture',reason:'missing'}))},
 }).default;
 const render=()=>runtime.render(Page);await component(render(),'PersonHero').props.onExportMedia();await find(render(),n=>n.type==='Button'&&text(n)==='Copy').props.onClick();render();await timers.shift()();
 assert.equal(messages.at(-1).message,expected);assert.equal(messages.at(-1).severity,severity);runtime.unmount();
});
for(const kind of ['Album','Event'])test(`${kind} stale errors cannot update another page`,async()=>{
 const p=entityPage(kind);p.render();p.route.id='2';p.render();p.second.resolve({id:2,name:'two',title:'two'});await settle();p.first.reject(Error('old failure'));await settle();
 assert.ok(!find(p.render(),n=>n.type==='Alert'&&text(n).includes('old failure')));p.runtime.unmount();
});
test('album rename completion after navigation cannot overwrite the next detail',async()=>{
 const rename=deferred();const p=entityPage('Album',{updateAlbum:()=>rename.promise});p.render();p.first.resolve({id:1,name:'one',media_count:1});await settle();
 const tree=p.render();find(tree,n=>n.type==='TextField').props.onChange({target:{value:'renamed'}});
 const pending=find(p.render(),n=>n.type==='Button'&&text(n)==='Rename').props.onClick();p.route.id='2';p.render();p.second.resolve({id:2,name:'two',media_count:1});await settle();
 rename.resolve({id:1,name:'renamed',media_count:1});await pending;
 assert.equal(text(find(p.render(),n=>n.type==='Typography'&&n.props.variant==='h5')),'two');p.runtime.unmount();
});
for(const kind of ['Album','Event'])test(`${kind} read completion after unmount cannot write state`,async()=>{
 const p=entityPage(kind);let unmounted=false,writes=0;const state=p.runtime.api.useState;
 p.runtime.api.useState=initial=>{const [value,setValue]=state(initial);return [value,next=>{if(unmounted)writes++;setValue(next);}];};
 p.render();p.runtime.unmount();unmounted=true;p.first.resolve({id:1,name:'old',title:'old'});await settle();assert.equal(writes,0);
});
test('event conversion completion after navigation does not navigate the new page',async()=>{
 const pending=deferred();const p=entityPage('Event',{convertEventToAlbum:()=>pending.promise});p.render();p.first.resolve({id:1,title:'one',media_count:1});await settle();
 find(p.render(),n=>n.type==='Button'&&text(n)==='Convert to album').props.onClick();
 const dialog=component(p.render(),'../components/ConfirmDialog');assert.equal(dialog.props.open,true);const promise=dialog.props.onConfirm();
 p.route.id='2';p.render();p.second.resolve({id:2,title:'two',media_count:1});await settle();pending.resolve({id:33});await promise;assert.deepEqual(p.calls,[]);p.runtime.unmount();
});
