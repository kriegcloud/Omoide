const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
function environment() {
 const cache=new Map();
 function load(relative) {
  const file=path.resolve(__dirname,'../src',relative); if(cache.has(file))return cache.get(file).exports;
  const module={exports:{}};cache.set(file,module);
  const code=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
  new Function('require','module','exports',code)(id=>id.endsWith('/config')?{API:''}:id.startsWith('.')?load(path.relative(path.resolve(__dirname,'../src'),path.resolve(path.dirname(file),id+'.ts'))):require(id),module,module.exports);return module.exports;
 }
 return {...load('services/geographyOperations.ts'),...load('stores/optimisticOperations.ts'),...load('services/api.ts')};
}
const tick=()=>new Promise(setImmediate);
for(const surface of ['event','scene','geolocation','timeline']) {
 test(`${surface}: intent is immediate, definitive rollback and unknown revalidation never replay`,async()=>{
  const e=environment();let reject,calls=0,refresh=0;
  const key=surface==='geolocation'?'media:1':surface==='scene'?'scene:1':'event:1';
  const op=e.startGeographyMutation('Save',key,[{kind:'fields',key,values:{title:'Pending'}}],()=>{calls++;return new Promise((_,no)=>reject=no);},async()=>{refresh++;});
  assert.equal(e.projectEntity(key,{title:'Before'}).title,'Pending');await tick();reject(new e.DefinitiveMutationError('rejected'));
  assert.equal((await op.done).state,'rejected');assert.equal(e.projectEntity(key,{title:'Before'}).title,'Before');
  const unknown=e.startGeographyMutation('Save',key,[],async()=>{calls++;throw new TypeError('lost');},async()=>{refresh++;});
  await unknown.done;await tick();assert.equal(refresh,1);assert.equal(calls,2);
 });
}
test('scene confirmation dismisses before transport resolves',()=>{
 const {runtime,load,nodes,button,deferred,flush}=require('./datasetRuntime.cjs');
 const rt=runtime(),pending=deferred();let started=0;
 const Component=load('components/SceneManager.tsx',rt,{
  '../services/scene':{getScenes:async()=>[],createScene:()=>pending.promise},
  '../services/geographyOperations':{startGeographyMutation:()=>{started++;return {done:pending.promise};}},
  '../config':{__esModule:true,default:{},API:''},
 }).SceneManager;
 const render=()=>rt.render(()=>Component({mediaId:1,duration:120,persons:[],onSeekRequest(){}}));
 let tree=render();button(tree,'Add Scene').props.onClick();tree=render();
 const input=nodes(tree,n=>n.type==='TextField'&&n.props.label?.startsWith('Start'))[0];input.props.onChange({target:{value:'10'}});tree=render();
 const create=nodes(tree,n=>n.type==='Button'&&n.props.onClick?.name==='handleCreateScene')[0];create.props.onClick();tree=render();
 assert.equal(nodes(tree,n=>n.type==='Dialog')[0].props.open,false);assert.equal(started,1);
});
test('bulk event receipt restores skipped IDs without restoring successful removals',async()=>{
 const e=environment();
 const op=e.startGeographyMutation('Delete events','event:1',[{kind:'remove',key:'event:1',query:'events'},{kind:'remove',key:'event:2',query:'events'}],async()=>({deleted_ids:[1]}),async()=>{},result=>result.deleted_ids.map(id=>`event:${id}`));
 assert.deepEqual(e.projectList('events','event',[{id:1},{id:2}]).items,[]);
 assert.equal((await op.done).state,'partially_committed');
 assert.deepEqual(e.projectList('events','event',[{id:1},{id:2}]).items,[{id:2}]);
});
test('unknown geography keeps intent and visible refresh error when authoritative read fails',async()=>{
 const e=environment();
 const op=e.startGeographyMutation('Move location','media:9',[{kind:'fields',key:'media:9',values:{latitude:50}}],async()=>{throw Error('lost response');},async()=>{throw Error('offline read');});
 await op.done;await tick();
 const record=e.optimisticOperations.getSnapshot().find(row=>row.id===op.id);
 assert.equal(record.state,'unknown');assert.match(record.refreshError,/offline read/);
 assert.equal(e.projectEntity('media:9',{latitude:0}).latitude,50);
});
test('event title patches before transport resolves and definitive rejection restores base title',async()=>{
 const {hooks,load,find,deferred,settle}=require('./detailRuntime.cjs');
 const rt=hooks(),e=environment(),pending=deferred();
 const Page=load('pages/EventDetailPage.tsx',rt,{
  '../config':{__esModule:true,default:{EVENTS_ENABLED:true},API:''},
  '../stores/optimisticOperations':e,
  '../services/geographyOperations':e,
  'react-router-dom':{useParams:()=>({id:'1'}),useNavigate:()=>()=>{}},
  '../services/features':{getEvent:async()=>({id:1,title:'Before',start_at:'2020-01-01',end_at:'2020-01-02',media_count:1}),updateEvent:()=>pending.promise},
 }).default;
 const render=()=>rt.render(Page);render();await settle();
 find(render(),n=>n.props?.['aria-label']==='Rename event').props.onClick();
 find(render(),n=>n.type==='TextField').props.onChange({target:{value:'Intent'}});
 find(render(),n=>n.type==='Button'&&n.props.children==='Save').props.onClick();
 assert.equal(render().props.title,'Intent');assert.equal(find(render(),n=>n.type==='TextField'),undefined);
 pending.reject(new e.DefinitiveMutationError('refused'));await new Promise(setImmediate);
 assert.equal(render().props.title,'Before');
 assert.equal(e.optimisticOperations.getSnapshot().at(-1).state,'rejected');rt.unmount();
});
test('audited scene validation rolls back while geolocation filesystem permission errors reconcile',async()=>{
 const e=environment();let reads=0;
 const rejected=e.startGeographyMutation('Create scene','scene:1',[],async()=>{throw new e.ApiError('Not a video',400);},async()=>{reads++;});
 assert.equal((await rejected.done).state,'rejected');assert.equal(reads,0);
 const unknown=e.startGeographyMutation('Save GPS','media:1',[],async()=>{throw new e.ApiError('File write permission error',403);},async()=>{reads++;});
 assert.equal((await unknown.done).state,'unknown');await tick();assert.equal(reads,1);
});
