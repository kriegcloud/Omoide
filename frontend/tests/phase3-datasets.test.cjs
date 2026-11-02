const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),ts=require('typescript');
const {runtime,load,nodes,button,deferred,flush}=require('./datasetRuntime.cjs');
function env(){const cache=new Map();function read(rel){const file=path.resolve(__dirname,'../src',rel);if(cache.has(file))return cache.get(file).exports;const mod={exports:{}};cache.set(file,mod);const js=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText;new Function('require','module','exports',js)(id=>id.endsWith('/config')?{API:''}:id.startsWith('.')?read(path.relative(path.resolve(__dirname,'../src'),path.resolve(path.dirname(file),id+'.ts'))):require(id),mod,mod.exports);return mod.exports;}return {service:read('services/datasets.ts'),core:read('stores/optimisticOperations.ts')};}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('dataset item intent patches immediately, rolls back a definitive rejection and reports globally',async()=>{const {service,core}=env(),d=deferred();global.fetch=()=>d.promise;const request=service.updateDatasetItem(1,2,{excluded:true});const rejected=assert.rejects(request,/denied/);assert.equal(core.projectEntity('dataset:1/item/2',{excluded:false}).excluded,true);d.resolve(new Response(JSON.stringify({detail:'denied'}),{status:403}));await rejected;assert.equal(core.projectEntity('dataset:1/item/2',{excluded:false}).excluded,false);assert.equal(core.optimisticOperations.getSnapshot().at(-1).state,'rejected');});
test('unknown dataset outcome reads authoritative dataset once without replaying a write',async()=>{const {service,core}=env();const calls=[];global.fetch=async(url,init)=>{calls.push([url,init?.method??'GET']);if(init?.method)throw new TypeError('lost response');return new Response(JSON.stringify({id:1}));};await assert.rejects(service.updateDatasetItem(1,2,{weight:2}),/lost response/);await tick();assert.deepEqual(calls.map(c=>c[1]),['PATCH','GET','GET']);assert.equal(core.optimisticOperations.getSnapshot().at(-1).state,'reconciled');});
test('new dataset dismisses immediately while creation remains pending',async()=>{const rt=runtime(),d=deferred();let closed=0;const Component=load('components/NewDatasetDialog.tsx',rt,{'../services/datasets':{createDataset:()=>d.promise}}).default;const render=()=>rt.render(()=>Component({open:true,onClose:()=>closed++,onCreated(){}}));let tree=render();nodes(tree,n=>n.type==='TextField'&&n.props.label==='Name')[0].props.onChange({target:{value:'Example'}});tree=render();button(tree,'Create dataset').props.onClick();assert.equal(closed,1);d.resolve({id:1});await flush();});
test('batch crop dismisses before the request settles',async()=>{const rt=runtime(),d=deferred();let closed=0;const Component=load('components/BatchCropDialog.tsx',rt,{'../services/datasets':{batchCropDatasetItems:()=>d.promise},'../services/mediaActions':{getFaceCropSuggestions:async()=>[]}}).default;let tree=rt.render(()=>Component({open:true,datasetId:1,items:[],onClose:()=>closed++,onApplied(){}}));const apply=nodes(tree,n=>n.type==='Button'&&/Apply/.test(require('./datasetRuntime.cjs').text(n))).at(-1);apply.props.onClick();assert.equal(closed,1);d.resolve({});await flush();});

test('dataset removal hides immediately and restores skipped members from the receipt',async()=>{
  const {service,core}=env(),d=deferred();global.fetch=()=>d.promise;
  const request=service.removeDatasetItems(1,[10,20]);
  assert.equal(core.projectEntity('dataset:1/media/10',{id:10}),null);
  assert.equal(core.projectEntity('dataset:1/media/20',{id:20}),null);
  d.resolve(new Response(JSON.stringify({added_ids:[10],skipped_ids:[20]})));
  await request;
  assert.equal(core.projectEntity('dataset:1/media/10',{id:10}),null);
  assert.deepEqual(core.projectEntity('dataset:1/media/20',{id:20}),{id:20});
  assert.equal(core.optimisticOperations.getSnapshot().at(-1).state,'partially_committed');
});

test('dataset training request has immediate job submission feedback and accepted result',async()=>{
  const {service,core}=env(),d=deferred();global.fetch=()=>d.promise;
  const request=service.createTrainingRun(1,{export_id:2,base_model:'fixture'});
  assert.equal(core.optimisticOperations.getSnapshot().at(-1).feedback,'job');
  d.resolve(new Response(JSON.stringify({id:3,status:'pending'})));
  assert.equal((await request).id,3);
  assert.equal(core.optimisticOperations.getSnapshot().at(-1).state,'committed');
});

test('new dataset has a provisional row which is removed on definitive failure',async()=>{
 const {service,core}=env(),d=deferred();global.fetch=()=>d.promise;
 const request=service.createDataset({name:'New dataset'});const rejected=assert.rejects(request,/denied/);
 const rows=core.projectList('datasets','dataset',[]).items;
 assert.equal(rows.length,1);assert.equal(rows[0].name,'New dataset');assert.ok(rows[0].id<0);
 d.resolve(new Response(JSON.stringify({detail:'denied'}),{status:403}));await rejected;
 assert.deepEqual(core.projectList('datasets','dataset',[]).items,[]);
});

test('queued dataset input is snapshotted before a caller changes its draft',async()=>{
 const {service}=env(),first=deferred();const requests=[];
 global.fetch=async(url,init)=>{requests.push(JSON.parse(init.body));if(requests.length===1)return first.promise;return new Response(JSON.stringify({id:2,weight:2}));};
 const one=service.updateDatasetItem(1,2,{weight:1});const input={weight:2};const two=service.updateDatasetItem(1,2,input);input.weight=9;
 await tick();first.resolve(new Response(JSON.stringify({id:2,weight:1})));await one;await two;
 assert.deepEqual(requests,[{weight:1},{weight:2}]);
});

test('reopening dataset creation while an earlier request is pending preserves the new draft',async()=>{
 const rt=runtime(),d=deferred();let open=true;const Component=load('components/NewDatasetDialog.tsx',rt,{'../services/datasets':{createDataset:()=>d.promise}}).default;
 const render=()=>rt.render(()=>Component({open,onClose:()=>{open=false;},onCreated(){}}));
 let tree=render();nodes(tree,n=>n.type==='TextField'&&n.props.label==='Name')[0].props.onChange({target:{value:'First'}});button(render(),'Create dataset').props.onClick();
 render();open=true;tree=render();nodes(tree,n=>n.type==='TextField'&&n.props.label==='Name')[0].props.onChange({target:{value:'Second'}});
 d.resolve({id:1});await flush();tree=render();
 assert.equal(nodes(tree,n=>n.type==='TextField'&&n.props.label==='Name')[0].props.value,'Second');
 assert.equal(button(tree,'Create dataset').props.disabled,false);
});
