const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {test}=require('node:test');const ts=require('typescript');
const {runtime,load,nodes,button,text,deferred,flush}=require('./datasetRuntime.cjs');
const caption=(id,value)=>({item_id:id,caption:value,media:{id,thumbnail_path:'fixture.jpg'},findings:[],source:'override',review_status:'approved'});
test('caption filter rejects an obsolete response that ignores abort',async()=>{
 const rt=runtime(),old=deferred(),fresh=deferred();const Panel=load('components/DatasetCaptionsPanel.tsx',rt,{'../services/datasets':{getDatasetCaptions:(_,filter)=>filter==='all'?old.promise:fresh.promise},'../urlUtils':{encodeFilePath:encodeURIComponent}}).default;
 const render=()=>rt.render(()=>Panel({datasetId:1}));nodes(render(),n=>n.type==='Select')[0].props.onChange({target:{value:'approved'}});render();fresh.resolve({items:[caption(2,'current')],next_cursor:null});await flush();render();old.resolve({items:[caption(1,'obsolete')],next_cursor:null});await flush();assert.equal(nodes(render(),n=>n.type==='TextField')[0].props.value,'current');rt.unmount();
});
test('caption regeneration stops polling after unmount',async()=>{
 const timers=[];let reads=0;global.window={setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){}};const rt=runtime();
 const Panel=load('components/DatasetCaptionsPanel.tsx',rt,{'../services/datasets':{getDatasetCaptions:async()=>({items:[caption(1,'text')],next_cursor:null})},'../services/annotations':{startAnnotation:async()=>({id:'attempt'}),getMediaAnnotations:async()=>{reads++;return{attempts:[{id:'attempt',status:'succeeded'}]}}},'../urlUtils':{encodeFilePath:encodeURIComponent}}).default;
 const render=()=>rt.render(()=>Panel({datasetId:1}));render();await flush();button(render(),'Regenerate').props.onClick();await flush();rt.unmount();timers.shift()();await flush();assert.equal(reads,0);
});
function detail(extra={}){const rt=runtime();let id='1';const dataset=id=>({id,name:'Dataset '+id,buckets:[512],item_count:0,included_count:0,export_layout:'ai_toolkit'});const Page=load('pages/DatasetDetailPage.tsx',rt,{'react-router-dom':{useParams:()=>({id}),useNavigate:()=>()=>{}},'../services/datasets':{getDataset:async id=>dataset(id),getDatasetItems:async()=>({items:[],next_cursor:null}),getDatasetExports:async()=>[],getTrainingRuns:async()=>[],getDatasetAnalysis:async()=>({}),getDatasetGaps:async()=>[],...extra}}).default;return{rt,render:()=>rt.render(Page),route:next=>id=next};}
test('dataset export creation does not attach old export history to a new dataset',async()=>{
 const pending=deferred(),h=detail({createDatasetExport:()=>pending.promise});h.render();await flush();nodes(h.render(),n=>n.type==='Tabs')[0].props.onChange(null,2);const started=button(h.render(),'Export now').props.onClick();h.route('2');h.render();await flush();pending.resolve({id:10,dataset_id:1,status:'completed',layout:'ai_toolkit',item_count:0,output_dir:'old-dataset-export'});await started;assert.ok(!text(h.render()).includes('old-dataset-export'));h.rt.unmount();
});
function editorModule(){const file=path.resolve(__dirname,'../src/utils/editorOps.ts');const code=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;const mod={exports:{}};new Function('exports','module',code)(mod.exports,mod);return mod.exports;}
test('image editor save validates rendered canvas geometry rather than unrotated metadata',()=>{
 const filename=path.resolve(__dirname,'../src/components/ImageEditorDialog.tsx');const source=ts.createSourceFile(filename,fs.readFileSync(filename,'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);let initializer;function visit(n){if(ts.isVariableDeclaration(n)&&n.name.getText(source)==='ops'&&n.initializer?.getText(source).startsWith('designStateToOps('))initializer=n.initializer.getText(source);ts.forEachChild(n,visit);}visit(source);
 const ops=editorModule();const invoke=new Function('designStateToOps','editorCanvasOutput','freshState','media','current','EDITOR_SAVING_PIXEL_RATIO','return '+initializer);
 assert.deepEqual(invoke(ops.designStateToOps,ops.editorCanvasOutput,{adjustments:{rotation:90}},{width:640,height:480},{imageData:{width:640,height:480,imageCanvas:{width:480,height:640}}},1),[{op:'rotate',degrees:90}]);
});
test('video thumbnail preview exposes a named native button before activation',async()=>{
 const {hooks,load:loadDetail,find}=require('./detailRuntime.cjs');const rt=hooks();const previous=global.fetch;global.fetch=async()=>({text:async()=>''});global.window={localStorage:{getItem:()=>null}};
 try{const {VideoWithPreview}=loadDetail('components/VideoPlayer.tsx',rt,{'../urlUtils':{encodeFilePath:encodeURIComponent}});const render=()=>rt.render(()=>VideoWithPreview({media:{id:9,path:'fixture.mp4',filename:'fixture.mp4',duration:3}}));const play=find(render(),n=>n.type==='Button'&&n.props.type==='button'&&n.props['aria-label']?.includes('Play'));assert.ok(play,'preview must offer an accessible Play button');play.props.onClick();assert.ok(find(render(),n=>n.type==='../localFilePlayer'));rt.unmount();await flush();}finally{global.fetch=previous;}
});
test('installed editor dependency retains full crop through repeated viewport fits and rotation',()=>{
 const render=require('./editorDependencyRuntime.cjs'),ops=editorModule();
 let state={shownImageDimensions:{width:568.3,height:426.225,scaledBy:1},adjustments:{crop:{x:0,y:0,width:568.3,height:426.225},rotation:90}};
 const next={...state,shownImageDimensions:{width:503.4967805989583,height:377.6225854492187,scaledBy:.9059044}};
 assert.deepEqual(render(next).imageData.imageCanvas,{width:542,height:722},'reproduce installed provider stale-coordinate defect');
 for(const width of [503.4967805989583,400,568.3,350]){
  const current={...state,shownImageDimensions:{width,height:width*.75,scaledBy:1}};
  current.adjustments=ops.refitEditorCrop(state,current)??current.adjustments;
  const exported=render(current).imageData;
  assert.deepEqual(ops.designStateToOps(current,640,480,ops.editorCanvasOutput(exported)),[{op:'rotate',degrees:90}]);
  state=current;
 }
});
test('installed editor refit preserves partial crop and explicit target resize',()=>{
 const render=require('./editorDependencyRuntime.cjs'),ops=editorModule();
 let state={shownImageDimensions:{width:640,height:480,scaledBy:1},adjustments:{crop:{x:80,y:60,width:320,height:240},rotation:90},resize:{width:256,height:384}};
 for(const width of [568.3,400,800]){
  const current={...state,shownImageDimensions:{width,height:width*.75,scaledBy:1}};
  current.adjustments=ops.refitEditorCrop(state,current)??current.adjustments;
  const exported=render(current).imageData;
  assert.deepEqual(ops.designStateToOps(current,640,480,ops.editorCanvasOutput(exported)),[{op:'crop',x:80,y:60,width:320,height:240},{op:'rotate',degrees:90},{op:'resize',width:256,height:384}]);state=current;
 }
 const edited={...state,adjustments:{...state.adjustments,crop:{x:10,y:10,width:100,height:100}},shownImageDimensions:{width:400,height:300,scaledBy:1}};
 assert.equal(ops.refitEditorCrop(state,edited),undefined,'new user crop must not be rescaled as an old crop');
});
