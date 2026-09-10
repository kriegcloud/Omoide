const {test}=require('node:test');const assert=require('node:assert/strict');
const {hooks,load,component,deferred,settle,noop}=require('./detailRuntime.cjs');
function bus(){const listeners=new Set();return {emit:e=>listeners.forEach(f=>f(e)),subscribe:f=>{listeners.add(f);return()=>listeners.delete(f)}}}
test('cursor grid receives card deletion and blocks stale page resurrection',async()=>{
 const runtime=hooks(),events=bus(),pending=deferred();
 const Component=load('components/CursorMediaGrid.tsx',runtime,{
  '../stores/mutationBus':{mutationBus:events},
  '../stores/listReconciliation':requireTranspiled('stores/listReconciliation.ts'),
  '../context/SelectionContext':{useSelection:()=>({selectedIds:new Set(),setSelected:noop,beginSelecting:noop,clear:noop})},
  '../hooks/useMarqueeSelection':{useGridSelection:()=>({})},
 }).CursorMediaGrid;
 const fetcher=()=>pending.promise;const render=()=>runtime.render(()=>Component({listKey:'album-1',fetcher}));
 render();events.emit({type:'media:deleted',ids:[7]});pending.resolve({items:[{id:7},{id:8}],next_cursor:null});await settle();
 const view=render();const grid=component(view,'react-masonry-css');const cards=[grid.props.children].flat();assert.deepEqual(cards.map(n=>n.props.children.props.media.id),[8]);
 events.emit({type:'media:deleted',ids:[8]});assert.equal(component(render(),'react-masonry-css'),undefined);
});
function requireTranspiled(file){return load(file,hooks());}
test('bulk resolve reconciles processed ids and uses refetch mode for count-only results',async()=>{
 for(const [response,expected] of [[{removed:1,processed_ids:[7]},[[7],1,false]],[{removed:1},[[],1,true]]]){
  const runtime=hooks();let actual;
  const Toolbar=load('components/BulkResolveToolbar.tsx',runtime,{'../hotkeys/useHotkey':{useHotkeys:noop,useHotkey:noop},'../hotkeys/keymap':{selectionBindings:[]},'../formatUtils':{formatBytes:String}}).default;
  const props={total:2,shownCount:2,shownSize:0,selectedIds:new Set([7,8]),onFeedback:noop,onResolved:(...args)=>actual=args,resolve:async()=>response};
  const render=()=>runtime.render(()=>Toolbar(props));let view=render();
  const {find}=require('./detailRuntime.cjs');find(view,n=>n.type==='Button'&&n.props.children==='Remove records').props.onClick();
  await component(render(),'./ConfirmDialog').props.onConfirm();assert.deepEqual(actual,expected);
 }
});
test('EXIF distinguishes failed load from empty metadata',async()=>{
 const runtime=hooks();const Exif=load('components/MediaExif.tsx',runtime,{'../services/exif':{getExifData:async()=>{throw Error('offline')}}}).MediaExif;
 const render=()=>runtime.render(()=>Exif({mediaId:7}));render();await settle();const state=component(render(),'./ListState');assert(state?.props.error);assert.equal(typeof state.props.onRetry,'function');
});
test('related-content failure is an error rather than an empty successful list',async()=>{
 const runtime=hooks();const Related=load('components/MediaRelatedContent.tsx',runtime,{
  '../services/media':{getSimilarMedia:async()=>{throw Error('offline')}},
  '../stores/listReconciliation':requireTranspiled('stores/listReconciliation.ts'),
  '../context/SelectionContext':{useSelection:()=>({selectedIds:new Set(),setSelected:noop,beginSelecting:noop,clear:noop})},
  '../hooks/useMarqueeSelection':{useGridSelection:()=>({})},
 }).default;
 const render=()=>runtime.render(()=>Related({mediaId:7}));render();await settle();const state=component(render(),'./ListState');assert(state?.props.error);assert.equal(typeof state.props.onRetry,'function');
});
test('cursor pagination does not restore the old total after a deletion in an earlier page',async()=>{
 const runtime=hooks();runtime.api.useId=()=> 'cursor-test';const events=bus(),pending=deferred();let visible=false;
 const hook=load('hooks/useCursorList.ts',runtime,{
  '../stores/mutationBus':{mutationBus:events},'../stores/listReconciliation':requireTranspiled('stores/listReconciliation.ts'),
  'react-intersection-observer':{useInView:()=>({ref:noop,inView:visible})},
 }).useCursorList;
 const fetcher=cursor=>cursor?pending.promise:Promise.resolve({items:[{id:1}],next_cursor:'next',total:10});const render=()=>runtime.render(()=>hook(fetcher));
 render();await settle();assert.equal(render().total,10);visible=true;render();events.emit({type:'media:deleted',ids:[1]});assert.equal(render().total,9);
 pending.resolve({items:[{id:2}],next_cursor:null,total:10});await settle();assert.equal(render().total,9);
});
