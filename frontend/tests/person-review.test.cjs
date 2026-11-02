const test = require('node:test');
const assert = require('node:assert/strict');
const { runtime, load, nodes } = require('./datasetRuntime.cjs');

test('No faces retains the former review endpoint and all-unlinked scope', async () => {
  const rt = runtime(); const calls=[];
  const service=load('services/nofaces.ts',rt,{'../stores/mutationBus':{mutationBus:{emit(){}}}});
  const prior=global.fetch;
  global.fetch=async(url)=>{calls.push(url);return {ok:true,json:async()=>({items:[],total:0,next_cursor:null})};};
  try {await service.getNoFacesMedia({scope:'all',folder:'/photos',mediaType:'video'});}
  finally{global.fetch=prior;}
  assert.match(calls[0],/\/api\/nofaces\?/);
  assert.match(calls[0],/scope=all/);
  assert.match(calls[0],/media_type=video/);
});

test('Person-free review separates processed and pending, URL filters and score captions', () => {
 const rt=runtime(); let options;
 const page=load('pages/PersonReviewPage.tsx',rt,{
  '../hooks/useCursorList':{useCursorList:(fetcher,key,opts)=>{options=opts;return {items:[{id:7,filename:'sample',size:12,person_score:0.12,person_siglip_margin:-0.2}],total:1,selectedIds:new Set(),refetch:async()=>{},clearSelection(){}};}},
  '../components/RerunProcessorsDialog':{RerunProcessorsDialog:'RerunProcessorsDialog'},
  '../formatUtils':{formatBytes:n=>`${n} B`},
  '../stores/queryKeys':{queryKey:(name)=>name},
  '../hotkeys/useHotkey':{useHotkey(){}},
  'react-router-dom':{useSearchParams:()=>[new URLSearchParams('scope=pending&media_type=video'),()=>{}]},
 }).default;
 const tree=rt.render(()=>page({kind:'nopersons'}));
 assert.equal(options.queryKey,'nopersons');
 assert.equal(options.entityKind,'media');
 const tabs=nodes(tree,n=>n.type==='Tab');
 assert.deepEqual(tabs.map(n=>n.props.value),['processed','pending']);
 const tile=nodes(tree,n=>n.props?.filename==='sample')[0];
 assert.match(tile.props.caption,/0.120/);
 assert.match(tile.props.caption,/-0.200/);
});


test('Resolve publishes removal intent immediately and snapshots filtered selection with operation id', async () => {
 const rt=runtime(); let command; let payload;
 const page=load('pages/PersonReviewPage.tsx',rt,{
  '../hooks/useCursorList':{useCursorList:()=>({items:[{id:7,filename:'sample',size:12}],total:1,selectedIds:new Set([7]),refetch:async()=>{},clearSelection(){}})},
  '../components/RerunProcessorsDialog':{RerunProcessorsDialog:'RerunProcessorsDialog'},
  '../formatUtils':{formatBytes:n=>`${n} B`},
  '../stores/queryKeys':{queryKey:name=>name,entityKey:(kind,id)=>`${kind}:${id}`},
  '../stores/optimisticOperations':{startOperation:c=>{command=c;return {id:'operation',done:Promise.resolve()};}},
  '../stores/mutationBus':{mutationBus:{emit(){}}},
  '../hotkeys/useHotkey':{useHotkey(){}},
  '../services/nopersons':{resolveNoPersons:async p=>{payload=p;return {removed:1,processed_ids:[7]};}},
  'react-router-dom':{useSearchParams:()=>[new URLSearchParams('scope=pending&media_type=video&folder=%2Fphotos'),()=>{}]},
 }).default;
 const tree=rt.render(()=>page({kind:'nopersons'}));
 const toolbar=nodes(tree,n=>typeof n.props?.resolve==='function')[0];
 await toolbar.props.resolve({action:'DELETE_RECORDS',mediaIds:[7]});
 assert.equal(toolbar.props.deferredFeedback,true);
 assert.deepEqual(command.patches,[{kind:'remove',key:'media:7'}]);
 assert.deepEqual(command.input.media_ids,[7]);
 const result=await command.request(command.input,'op-42');
 assert.equal(payload.operation_id,'op-42');
 assert.equal(payload.scope,'pending');
 assert.equal(payload.media_type,'video');
 assert.equal(payload.folder,'/photos');
 assert.deepEqual(command.receipt(result).applied,['media:7']);
 assert.equal(command.receipt(result).state,'succeeded');
});

function operationEnvironment() {
 const fs=require('node:fs'),path=require('node:path'),ts=require('typescript'),cache=new Map();
 function read(relative){
  const file=path.resolve(__dirname,'../src',relative);
  if(cache.has(file))return cache.get(file).exports;
  const module={exports:{}};cache.set(file,module);
  const js=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText;
  new Function('require','module','exports',js)(name=>name.startsWith('.')?read(path.relative(path.resolve(__dirname,'../src'),path.resolve(path.dirname(file),name+'.ts'))):require(name),module,module.exports);
  return module.exports;
 }
 return read('stores/optimisticOperations.ts');
}

for(const kind of ['nopersons','nofaces']) test(`${kind} optimistic resolve restores media on definitive rejection`,async()=>{
 const core=operationEnvironment(),rt=runtime();let handle;
 const service=load(`services/${kind}.ts`,rt,{'../stores/optimisticOperations':core});
 const prior=global.fetch;
 global.fetch=async()=>({ok:false,status:403,json:async()=>({detail:'Presentation mode'})});
 try {
 const page=load('pages/PersonReviewPage.tsx',rt,{
  '../hooks/useCursorList':{useCursorList:()=>({items:[{id:7,filename:'sample',size:12}],total:1,selectedIds:new Set([7]),refetch:async()=>{},clearSelection(){}})},
  '../components/RerunProcessorsDialog':{RerunProcessorsDialog:'RerunProcessorsDialog'},
  '../formatUtils':{formatBytes:n=>`${n} B`},
  '../stores/queryKeys':{queryKey:name=>name,entityKey:(kind,id)=>`${kind}:${id}`},
  '../stores/optimisticOperations':{startOperation:c=>{handle=core.startOperation(c);return handle;}},
  '../stores/mutationBus':{mutationBus:{emit(){}}},
  '../hotkeys/useHotkey':{useHotkey(){}},
  [`../services/${kind}`]:service,
 }).default;
 const tree=rt.render(()=>page({kind}));
 const toolbar=nodes(tree,n=>typeof n.props?.resolve==='function')[0];
 const request=toolbar.props.resolve({action:'DELETE_RECORDS',mediaIds:[7]});
 assert.deepEqual(core.projectList(kind,'media',[{id:7}],1).items,[]);
 await request;
 assert.equal((await handle.done).state,'rejected');
 assert.deepEqual(core.projectList(kind,'media',[{id:7}],1).items,[{id:7}]);
 }finally{global.fetch=prior;}
});
