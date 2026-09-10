const assert=require('node:assert/strict');
const {test}=require('node:test');
const {hooks,load,find,component,deferred,settle,listStore,noop}=require('./detailRuntime.cjs');
const media=id=>({media:{id,filename:`video-${id}.mp4`,duration:10},persons:[],orphans:[]});
function mediaPage(fetch, overrides={}) {
  const runtime=hooks(); const route={id:'1',key:'1',state:{mediaListKey:'images'}}; const store=listStore([media(1).media]);
  const Page=load('pages/MediaDetailPage.tsx',runtime,{
    'react-router-dom':{useParams:()=>({id:route.id}),useLocation:()=>route,useNavigate:()=>noop},
    '../services/media':{getMedia:fetch},'../stores/useListStore':store.module,...overrides}).default;
  return {runtime,route,store,render:()=>runtime.render(()=>Page())};
}
test('failed media navigation clears the previous detail and displays its error',async()=>{
  const page=mediaPage(async id=>{if(id==='2')throw Error('Failed to load media (500)');return media(Number(id));});
  page.render();await settle();assert.equal(component(page.render(),'MediaDisplay').props.media.id,1);
  page.route.id='2';page.route.key='2';page.render();await settle();const view=page.render();
  assert.equal(component(view,'MediaDisplay'),undefined);
  assert.ok(component(view,'Alert'));
});
test('background media refresh cannot overwrite the next media',async()=>{
  const pending=deferred();let calls=0;const page=mediaPage(id=>id==='1'&&++calls>1?pending.promise:Promise.resolve(media(Number(id))));
  page.render();await settle();const refresh=component(page.render(),'MediaContentTabs').props.onDetailReload();
  page.route.id='2';page.route.key='2';page.render();await settle();pending.resolve(media(1));await refresh;
  assert.equal(component(page.render(),'MediaDisplay').props.media.id,2);
});
test('a seek request is not replayed on the next video',async()=>{
  const page=mediaPage(async id=>media(Number(id)));page.render();await settle();
  component(page.render(),'MediaContentTabs').props.onSeekRequest(7);assert.equal(component(page.render(),'MediaDisplay').props.seekRequest.time,7);
  page.route.id='2';page.route.key='2';page.render();await settle();
  assert.equal(component(page.render(),'MediaDisplay').props.seekRequest,null);
});
test('failed delete restores the current detail and keeps cached media',async()=>{
  const page=mediaPage(async id=>media(Number(id)),{'../services/mediaActions':{deleteMediaRecord:async()=>{throw Error('forced failure');}}});
  page.render();await settle();component(page.render(),'MediaHeader').props.onOpenDialog('deleteRecord');
  await component(page.render(),'ActionDialogs').props.onConfirmDeleteRecord();
  assert.deepEqual(page.store.state.lists.images.items.map(item=>item.id),[1]);
  assert.equal(component(page.render(),'MediaDisplay').props.media.id,1);
});
test('a missing media response publishes deletion to heal all cached lists',async()=>{
  const events=[];const missing=Object.assign(Error('Failed to load media (404)'),{status:404});
  const page=mediaPage(async()=>{throw missing;},{'../stores/mutationBus':{mutationBus:{emit:event=>events.push(event),subscribe:()=>noop}}});
  page.render();await settle();assert.deepEqual(events,[{type:'media:deleted',ids:[1]}]);
});
function personPage(fetch, overrides={}) {
  const runtime=hooks();const route={id:'1',state:{}};const store=listStore();const registry=new Map();
  const hook=load('hooks/usePersonDetailPage.ts',runtime,{
    'react-router-dom':{useParams:()=>({id:route.id}),useLocation:()=>route,useNavigate:()=>noop},
    '../services/person':{getPerson:fetch,getPersonMediaAppearances:async()=>({items:[]})},
    '../services/personActions':{getSuggestedFaces:async()=>[],getPersonFaces:async()=>({items:[]})},
    '../stores/useListStore':store.module,
    '../context/UndoContext':{useUndo:()=>({push:noop,refreshVisible:async()=>{}}),useUndoRefresh:(key,fn)=>registry.set(key,fn),useMutationRefresh:noop},
    ...overrides,
  }).usePersonDetailPage;
  global.window={addEventListener:noop,removeEventListener:noop};
  return {runtime,route,registry,store,render:()=>runtime.render(hook)};
}
test('a person refresh in flight cannot overwrite the next person',async()=>{
  const pending=deferred();let calls=0;const page=personPage(id=>id==='1'&&++calls>1?pending.promise:Promise.resolve({id:Number(id),name:`person-${id}`}));
  page.render();await settle();const refresh=page.render().handlePersonUpdate();
  page.route.id='2';page.render();await settle();pending.resolve({id:1,name:'old person'});await refresh;
  assert.equal(page.render().person.id,2);
});
test('person load failure provides a visible retryable error state',async()=>{
  const page=personPage(async()=>{throw Error('Person unavailable');});page.render();await settle();const state=page.render();
  assert.equal(state.loading,false);assert.equal(state.loadError,'Person unavailable');assert.equal(typeof state.retryDetail,'function');
  const runtime=hooks();const Page=load('pages/PersonDetailPage.tsx',runtime,{'../hooks/usePersonDetailPage':{usePersonDetailPage:()=>state}}).default;
  const view=runtime.render(()=>Page());assert.equal(component(view,'CircularProgress'),undefined);assert.ok(find(find(view,node=>node.type==='@mui/material/Alert').props.action,node=>node.props?.children==='Retry'));
});
test('orphan sentinel waits for loading and then resumes while still visible',()=>{
  const runtime=hooks();let loading=true;let calls=0;const store=listStore();store.state.lists['orphan-faces']={items:[{id:1}],hasMore:true,get isLoading(){return loading;}};store.state.loadMore=async()=>{calls++;};
  const Page=load('pages/OrphanFaces.tsx',runtime,{
    'react-router-dom':{useSearchParams:()=>[new URLSearchParams(),noop],useNavigate:()=>noop},
    '../stores/useListStore':store.module,
    '../services/face':{getOrphanFaceCount:async()=>1},
    'react-intersection-observer':{useInView:()=>({ref:noop,inView:true})},
  }).default;
  const shell=runtime.render(()=>Page());const All=shell.props.children[1].type;
  const render=()=>runtime.render(()=>All());render();assert.equal(calls,0);loading=false;render();assert.equal(calls,1);
});
test('timeline registers a cache refresher for undo',()=>{
  const runtime=hooks();const registry=[];const store=listStore();
  const Timeline=load('components/TimelineTab.tsx',runtime,{'../stores/useListStore':store.module,'../context/UndoContext':{useUndoRefresh:(...args)=>registry.push(args),useMutationRefresh:noop}}).TimelineTab;
  runtime.render(()=>Timeline({person:{id:7}}));assert.ok(registry.some(([key])=>key==='cache:person-7-timeline'));
});
function suggestionsPage(actions={}) {
  const runtime=hooks();const store=listStore();const item={id:11,face:{id:11,media_id:3},person_id:7,person_name:'Suggested',score:.7,pose_bin:'front'};
  store.state.lists['orphan-face-suggestions:0.50']={items:[item],isLoading:false,hasMore:false};
  const Page=load('components/OrphanFaceSuggestions.tsx',runtime,{'../stores/useListStore':store.module,'../services/faceActions':actions}).default;
  return {runtime,store,item,render:()=>runtime.render(()=>Page({minScore:.5,onMinScoreChange:noop}))};
}
function suggestionButton(page,text) {
  const grid=component(page.render(),'FaceGrid');const footer=grid.props.renderFooter(page.item.face);
  return find(footer,node=>node.props?.children===text);
}
test('Not this person rejects the displayed pair and removes it from the session view',async()=>{
  const rejected=[];const page=suggestionsPage({rejectFaceSuggestion:async(...args)=>rejected.push(args)});
  const button=suggestionButton(page,'Not this person');assert.ok(button);await button.props.onClick();await settle();
  assert.deepEqual(rejected,[[11,7]]);assert.equal(component(page.render(),'FaceGrid').props.faces.length,0);
});
test('Review later hides a suggestion for the session without assigning or rejecting it',()=>{
  const data=new Map();global.sessionStorage={getItem:key=>data.get(key)??null,setItem:(key,value)=>data.set(key,value)};
  const page=suggestionsPage();const button=suggestionButton(page,'Review later');assert.ok(button);button.props.onClick();
  assert.equal(component(page.render(),'FaceGrid').props.faces.length,0);
  const remount=suggestionsPage();assert.equal(component(remount.render(),'FaceGrid').props.faces.length,0);
  delete global.sessionStorage;
});
test('Choose another opens a picker and assigns the snapshotted face',async()=>{
  const assigned=[];const page=suggestionsPage({assignFace:async(...args)=>assigned.push(args)});
  const button=suggestionButton(page,'Choose another…');assert.ok(button);button.props.onClick();
  const picker=component(page.render(),'./PersonPicker');assert.ok(picker);await picker.props.onSelect({id:9,name:'Other'});await settle();
  assert.deepEqual(assigned,[[[11],9]]);
});
test('the latest media refresh wins when same-route responses finish out of order',async()=>{
  const older=deferred(),newer=deferred();let calls=0;
  const page=mediaPage(()=>++calls===1?Promise.resolve(media(1)):calls===2?older.promise:newer.promise);
  page.render();await settle();const reload=component(page.render(),'MediaContentTabs').props.onDetailReload;
  const first=reload(),second=reload();newer.resolve({...media(1),media:{...media(1).media,filename:'latest.mp4'}});await second;
  older.resolve(media(1));await first;assert.equal(component(page.render(),'MediaDisplay').props.media.filename,'latest.mp4');
});
test('a rejected suggestion request failure restores the suggestion and shows an error',async()=>{
  const pending=deferred();const page=suggestionsPage({rejectFaceSuggestion:()=>pending.promise});
  const request=suggestionButton(page,'Not this person').props.onClick();assert.equal(component(page.render(),'FaceGrid').props.faces.length,0);
  pending.reject(Error('Cannot save decision'));await request;
  assert.equal(component(page.render(),'FaceGrid').props.faces.length,1);
  assert.ok(find(page.render(),node=>node.type==='Alert'&&node.props.children==='Cannot save decision'));
});
test('confirming a captured delete target after navigation leaves the new detail visible',async()=>{
  const deleted=[];const page=mediaPage(async id=>media(Number(id)),{'../services/mediaActions':{deleteMediaRecord:async id=>deleted.push(id)}});
  page.render();await settle();component(page.render(),'MediaHeader').props.onOpenDialog('deleteRecord');
  page.route.id='2';page.route.key='2';page.render();await settle();
  await component(page.render(),'ActionDialogs').props.onConfirmDeleteRecord();
  assert.deepEqual(deleted,[1]);assert.equal(component(page.render(),'MediaDisplay')?.props.media.id,2);
});
for (const [method,service] of [['handleDeleteWrapper','deleteFace'],['handleDetachWrapper','detachFace'],['handleAssignWrapper','assignFace']]) {
  test(`person ${service} failure rolls back the locally hidden face`,async()=>{
    const pending=deferred();const face={id:11,media_id:3};
    const page=personPage(async id=>({id:Number(id),name:'person'}),{
      '../services/faceActions':{[service]:()=>pending.promise},
      '../services/personActions':{getSuggestedFaces:async()=>[face],getPersonFaces:async()=>({items:[face]})},
    });
    page.store.state.lists['/api/person/1/faces']={items:[face],hasMore:false,isLoading:false};
    page.render();await settle();const result=page.render()[method]([11],7).catch(error=>error);
    assert.equal(page.render().detectedFacesList.length,0);assert.equal(page.render().suggestedFaces.length,0);
    pending.reject(Error('forced failure'));await result;
    assert.equal(page.render().detectedFacesList.length,1);assert.equal(page.render().suggestedFaces.length,1);assert.equal(page.render().snackbar.severity,'error');
  });
}
test('an in-flight person visibility response cannot replace the next person',async()=>{
  const pending=deferred();const page=personPage(async id=>({id:Number(id),name:`person-${id}`}),{
    '../services/personActions':{getSuggestedFaces:async()=>[],getPersonFaces:async()=>({items:[]}),hidePerson:()=>pending.promise},
  });
  page.render();await settle();const request=page.render().handleHideToggle();
  page.route.id='2';page.render();await settle();pending.resolve({id:1,name:'old hidden person',hidden_at:'today'});await request;
  assert.equal(page.render().person?.id,2);
});
