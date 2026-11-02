const test = require('node:test');
const assert = require('node:assert/strict');
const {hooks,load,deferred,settle,find,noop} = require('./detailRuntime.cjs');
function refreshHarness(runtime) {
  let subscriptions=[];
  return {module:{useMutationRefresh(types,callback){subscriptions.push({types,callback});},useUndo:()=>({push:noop,refreshVisible:async()=>{}}),useUndoRefresh:noop},
    render(fn){subscriptions=[];return runtime.render(fn);},
    async emit(type){for(const sub of subscriptions)if(sub.types.includes(type))await sub.callback();}};
}
for(const [file,name,service,method,event,result] of [
  ['components/home/AlbumsPreviewWidget.tsx','AlbumsPreviewWidget','../../services/features','getAlbums','album:changed',[]],
  ['components/home/FavoritesStripWidget.tsx','FavoritesStripWidget','../../services/media','getFavorites','media:updated',{items:[]}],
  ['components/MemoriesRail.tsx','MemoriesRail','../services/features','getMemories','media:deleted',[]],
  ['pages/PlacesPage.tsx','default','../services/features','getPlaces','media:updated',[]],
]) test(`${name} authoritatively reloads on ${event} and drops the old response`,async()=>{
  const runtime=hooks(),refresh=refreshHarness(runtime),pending=[];
  const context=file.includes('/home/')?'../../context/UndoContext':'../context/UndoContext';
  const module=load(file,runtime,{[context]:refresh.module,[service]:{[method]:()=>{const d=deferred();pending.push(d);return d.promise;}},
    'react-router-dom':{useLocation:()=>({})},'../hooks/useLocalStorageBoolean':{useLocalStorageBoolean:()=>[false,noop]}});
  const render=()=>refresh.render(()=>module[name]());
  render();assert.equal(pending.length,1);
  await refresh.emit(event);render();assert.equal(pending.length,2,'committed change must reload mounted holder');
  pending[1].resolve(result);await settle();render();
  pending[0].resolve(file.includes('AlbumsPreview')?[{id:1,name:'obsolete',media_count:1}]:result);await settle();
  const tree=render();assert.equal(Boolean(find(tree,n=>n.props?.children==='obsolete')),false);
  runtime.unmount();
});

test('open album dialog reloads its choices without letting an old request restore deleted albums',async()=>{
 const runtime=hooks(),refresh=refreshHarness(runtime),pending=[];
 const {AddToAlbumDialog}=load('components/AddToAlbumDialog.tsx',runtime,{'../context/UndoContext':refresh.module,
 '../services/features':{getAlbums:()=>{const d=deferred();pending.push(d);return d.promise;}}});
 const render=()=>refresh.render(()=>AddToAlbumDialog({open:true,mediaIds:[],onAdded:noop,onClose:noop}));
 render();await refresh.emit('album:changed');render();assert.equal(pending.length,2);
 pending[1].resolve([]);await settle();render();pending[0].resolve([{id:1,name:'obsolete',media_count:1}]);await settle();
 assert.equal(Boolean(find(render(),n=>n.props?.primary==='obsolete')),false);
});

test('folder destination reload preserves its navigation and creation draft',async()=>{
 const runtime=hooks(),refresh=refreshHarness(runtime);let calls=0;
 const {default:Picker}=load('components/FolderPickerDialog.tsx',runtime,{'../context/UndoContext':refresh.module,
 '../services/media':{getMediaFolders:async(path)=>{calls++;return {current_path:path??'',folders:[],breadcrumbs:[]};}}});
 const render=()=>refresh.render(()=>Picker({open:true,onConfirm:noop,onClose:noop}));
 render();await settle();let tree=render();
 find(tree,n=>n.type==='TextField').props.onChange({target:{value:'draft'}});render();
 await refresh.emit('media:moved');await settle();tree=render();
 assert.equal(calls,2);assert.equal(find(tree,n=>n.type==='TextField').props.value,'draft');
});

for (const [file,entity,event] of [['pages/AlbumDetailPage.tsx','Album','album:changed'],['pages/EventDetailPage.tsx','Event','events:changed']]) test(`${entity} metadata ignores an older initial response after a committed refresh`,async()=>{
 const runtime=hooks(),refresh=refreshHarness(runtime),pending=[];let id='1';
 const Page=load(file,runtime,{'../context/UndoContext':refresh.module,
 'react-router-dom':{useParams:()=>({id}),useNavigate:()=>noop},
 '../context/SelectionContext':{useSelection:()=>({selectedIds:new Set(),clear:noop})},
 '../services/features':{['get'+entity]:()=>{const d=deferred();pending.push(d);return d.promise;}}}).default;
 const render=()=>refresh.render(()=>Page());render();
 const committed=refresh.emit(event);pending[1].resolve({id:1,name:'new',title:'new',start_at:'2020-01-01',end_at:'2020-01-02'});await committed;await settle();render();
 pending[0].resolve({id:1,name:'obsolete',title:'obsolete',start_at:'2020-01-01',end_at:'2020-01-02'});await settle();
 assert.equal(Boolean(find(render(),n=>n.props?.children==='obsolete')),false);
 // A refresh already underway also cannot leak across navigation.
 const refreshAgain=refresh.emit(event);id='2';render();pending[3].resolve({id:2,name:'second',title:'second',start_at:'2020-01-01',end_at:'2020-01-02'});await settle();
 pending[2].resolve({id:1,name:'obsolete',title:'obsolete'});await refreshAgain;await settle();
 assert.equal(Boolean(find(render(),n=>n.props?.children==='obsolete')),false);
 runtime.unmount();
});

test('committed deletion clears an open media detail and invalidates its in-flight load',async()=>{
 const runtime=hooks(),pending=deferred(),listeners=new Set();
 const {listStore}=require('./detailRuntime.cjs');const store=listStore();
 const bus={emit(event){for(const fn of [...listeners])fn(event);},subscribe(fn){listeners.add(fn);return()=>listeners.delete(fn);}};
 const location={key:'1',state:{media:{id:1,filename:'old'}}};
 const Page=load('pages/MediaDetailPage.tsx',runtime,{'react-router-dom':{useParams:()=>({id:'1'}),useLocation:()=>location,useNavigate:()=>noop},
 '../stores/useListStore':store.module,'../stores/mutationBus':{mutationBus:bus},'../services/media':{getMedia:()=>pending.promise}}).default;
 const render=()=>runtime.render(()=>Page());render();bus.emit({type:'media:deleted',ids:[1]});
 pending.resolve({media:{id:1,filename:'old'},persons:[],orphans:[]});await settle();
 assert.equal(Boolean(find(render(),n=>n.type==='MediaDisplay')),false);
 assert.equal(find(render(),n=>n.type==='Alert').props.children,'This media is no longer available.');runtime.unmount();
});

test('visible EXIF refetches after committed media updates',async()=>{
 const runtime=hooks(),refresh=refreshHarness(runtime);let calls=0;
 const {MediaExif}=load('components/MediaExif.tsx',runtime,{'../context/UndoContext':refresh.module,'../services/exif':{getExifData:async()=>{calls++;return {};}}});
 const render=()=>refresh.render(()=>MediaExif({mediaId:1}));render();await settle();render();await refresh.emit('media:updated');render();await settle();assert.equal(calls,2);
});

test('possible merge queue reloads after a pair decision committed elsewhere',async()=>{
 const runtime=hooks(),refresh=refreshHarness(runtime);let calls=0;
 const Queue=load('components/MergeQueue.tsx',runtime,{'../context/UndoContext':refresh.module,'../hooks/useLocalStorageBoolean':{useLocalStorageBoolean:()=>[false,noop]},'../services/personActions':{getMergeCandidates:async()=>{calls++;return {items:[]};}}}).default;
 const render=()=>refresh.render(()=>Queue({onMerged:noop,refreshVersion:0}));render();await settle();render();await refresh.emit('person:changed');await settle();render();assert.equal(calls,2);
});

test('image search reruns its original upload to refresh ranked membership',async()=>{
 const runtime=hooks(),refresh=refreshHarness(runtime);const {listStore}=require('./detailRuntime.cjs');const store=listStore();let calls=0;
 const queryImage={name:'query.png'};const location={state:{items:[{id:1,thumbnail_path:'old'}],searchType:'image',queryImage}};
 const Page=load('pages/SearchResultPage.tsx',runtime,{'../stores/listReconciliation':load('stores/listReconciliation.ts',runtime), '../context/UndoContext':refresh.module,'../stores/useListStore':store.module,
 'react-router-dom':{useLocation:()=>location,useSearchParams:()=>[new URLSearchParams('category=media&query=Image Search')]},
 '../context/SelectionContext':{useSelection:()=>({selectedIds:new Set(),setSelected:noop,beginSelecting:noop,clear:noop})},
 '../hooks/useMarqueeSelection':{useGridSelection:()=>({})},'../services/media':{getMedia:async()=>({media:{id:1,thumbnail_path:'old'}})},
 '../services/searchActions':{searchByImage:async(file)=>{assert.equal(file,queryImage);calls++;return [{id:2,thumbnail_path:'new'}];}}}).default;
 const render=()=>refresh.render(()=>Page());render();await refresh.emit('media:updated');await settle();render();assert.equal(calls,1);
});

test('a committed timeline change clears its open day snapshot',async()=>{
 const runtime=hooks(),refresh=refreshHarness(runtime);const {listStore}=require('./detailRuntime.cjs');const store=listStore();
 store.state.lists['person-1-timeline']={items:[{type:'media',date:'2020-01-01',items:{id:1,inserted_at:'2020-01-01'}}],hasMore:false,isLoading:false};
 const {TimelineTab}=load('components/TimelineTab.tsx',runtime,{'../utils/serverDate': { parseServerDate: v => new Date(typeof v === 'string' && !/[zZ]|[+-]\d\d:?\d\d$/.test(v) && v.includes('T') ? v + 'Z' : v) },'../context/UndoContext':refresh.module,'../stores/useListStore':store.module});
 const render=()=>refresh.render(()=>TimelineTab({person:{id:1}}));
 find(render(),n=>n.type==='MediaItemGroup').props.onViewAll();assert.equal(find(render(),n=>n.type==='Dialog').props.open,true);
 await refresh.emit('media:moved');assert.equal(find(render(),n=>n.type==='Dialog').props.open,false);
});

test('person background refresh clears a deleted person',async()=>{
 const runtime=hooks(),refresh=refreshHarness(runtime);const {listStore}=require('./detailRuntime.cjs');const store=listStore();let missing=false;
 const hook=load('hooks/usePersonDetailPage.ts',runtime,{'../context/UndoContext':refresh.module,'../stores/useListStore':store.module,
 'react-router-dom':{useParams:()=>({id:'1'}),useLocation:()=>({state:{}}),useNavigate:()=>noop},
 '../services/person':{getPerson:async()=>{if(missing)throw Object.assign(Error('not found'),{status:404});return {id:1,name:'server name'};},getPersonMediaAppearances:async()=>({items:[]})},
 '../services/personActions':{getSuggestedFaces:async()=>[],getPersonFaces:async()=>({items:[]})}}).usePersonDetailPage;
 global.window={addEventListener:noop,removeEventListener:noop};
 const render=()=>refresh.render(hook);render();await settle();render();
 missing=true;await refresh.emit('person:changed');await settle();assert.equal(render().person,null);
});

 test('person edit form preserves a dirty rename across background name updates',()=>{
 const runtime=hooks();let name='server name';
 const {PersonEditForm}=load('components/PersonEditForm.tsx',runtime);
 const render=()=>runtime.render(()=>PersonEditForm({initialPersonData:{name},onSave:noop,saving:false}));
 find(render(),n=>n.type==='TextField').props.onChange({target:{name:'name',value:'my draft'}});render();
 name='server changed';assert.equal(find(render(),n=>n.type==='TextField').props.value,'my draft');
 });
