const assert=require('node:assert/strict'),{test}=require('node:test'),fs=require('node:fs'),path=require('node:path'),ts=require('typescript');
const root=path.resolve(__dirname,'..'),native=require('node:module').createRequire(root+'/node_modules/react-filerobot-image-editor/package.json');
function load(file,req=native){const mod={exports:{}};new Function('require','module','exports',ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText)(req,mod,mod.exports);return mod.exports;}
const ops=load(root+'/src/utils/editorOps.ts'),geometry=require('./editorDependencyRuntime.cjs');
function action(name,state,payload){return load(root+'/node_modules/react-filerobot-image-editor/lib/actions/'+name+'.js',id=>id.endsWith('extractCurrentDesignState')?s=>({adjustments:s.adjustments,resize:s.resize}):native(id)).default(state,payload);}
function fit(before,width){const current=action("setShownImageDimensions",before,{shownImageDimensions:{width,height:width*.75,scaledBy:1}});const patch=ops.refitEditorState(before,current);return{...current,...patch};}
function validate(state){return ops.designStateToOps(state,640,480,ops.editorCanvasOutput(geometry(state).imageData));}
for(const partial of [false,true])test('installed Undo/Redo keeps '+(partial?'partial':'full')+' crop coordinates across repeated viewport fits',()=>{
 const crop=partial?{x:80,y:60,width:320,height:240}:{x:0,y:0,width:640,height:480};
 const initial={adjustments:{crop,rotation:0},resize:{},shownImageDimensions:{width:640,height:480,scaledBy:1}};
 let state={...initial,adjustments:{...initial.adjustments,rotation:90},pastDesignStates:[{adjustments:initial.adjustments,resize:{}}],futureDesignStates:[]};
 for(const width of [568.3,503.4967805989583,400,800])state=fit(state,width);
 const undone=action('undo',state);undone.adjustments={...undone.adjustments,isFlippedX:true};
 const expectedCrop=partial?[{op:'crop',x:80,y:60,width:320,height:240}]:[];
 assert.deepEqual(validate(undone),[{op:'flip',axis:'horizontal'},...expectedCrop]);
 let redone=action('redo',undone);redone=fit(redone,300);
 assert.deepEqual(validate(redone),[...expectedCrop,{op:'rotate',degrees:90}]);
 redone.resize={width:256,height:384};assert.deepEqual(validate(redone).at(-1),{op:'resize',width:256,height:384});
});
const {observeEditorFits}=load(root+'/src/utils/editorFitObserver.ts',id=>id==='./editorOps'?ops:native(id));
const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
function frames(){let id=0;const queue=new Map();return{queue,schedule:fn=>{queue.set(++id,fn);return id},cancel:id=>queue.delete(id),async next(){const entry=queue.entries().next().value;assert.ok(entry,'scheduled observer frame');queue.delete(entry[0]);entry[1]();await flush();}};}
function snapshot(width=640){return{adjustments:{crop:{x:0,y:0,width:640,height:480},rotation:90},shownImageDimensions:{width,height:width*.75,scaledBy:1},pastDesignStates:[],futureDesignStates:[]};}
test('fit observer cleanup stops frames and ignores an outstanding read, including restart',async()=>{
 const f=frames();let resolve,applied=0;const pending=new Promise(yes=>resolve=yes);
 const stop=observeEditorFits(()=>pending,()=>applied++,f.schedule,f.cancel);
 await f.next();stop();resolve(snapshot());await flush();assert.equal(applied,0);assert.equal(f.queue.size,0);
 let state=snapshot();const stopAgain=observeEditorFits(async()=>state,()=>applied++,f.schedule,f.cancel);
 await f.next();assert.equal(applied,0);stopAgain();assert.equal(f.queue.size,0);
});
test('fit correction reducer callback is replay-safe and refuses intervening edits/history',async()=>{
 const f=frames();let state=snapshot(),update;const stop=observeEditorFits(async()=>state,fn=>update=fn,f.schedule,f.cancel);
 await f.next();state={...state,shownImageDimensions:{width:400,height:300,scaledBy:1}};await f.next();assert.ok(update);
 assert.deepEqual(update(state),update(state),'StrictMode may replay the reducer callback');
 assert.equal(update({...state,adjustments:{...state.adjustments,crop:{x:1,y:1,width:100,height:100}}}),null);
 assert.equal(update({...state,pastDesignStates:[{adjustments:{rotation:180}}]}),null);
 stop();assert.equal(update(state),null,'a queued correction after cleanup must be inert');assert.equal(f.queue.size,0);
});
function providerUpdate(state, update) {
 const old=global.HTMLElement;global.HTMLElement=class HTMLElement {};
 try{return load(root+'/node_modules/react-filerobot-image-editor/lib/actions/updateState.js',id=>id.endsWith('deepMerge')?load(root+'/node_modules/react-filerobot-image-editor/lib/utils/deepMerge.js'):native(id)).default(state,update);}
 finally{if(old===undefined)delete global.HTMLElement;else global.HTMLElement=old;}
}
test('observer retries a queued correction rejected by an intervening rotation without losing geometry',async()=>{
 const f=frames();let state=snapshot(),applies=0;const queue=[];
 const read=async()=>{while(queue.length)state=providerUpdate(state,queue.shift());return state;};
 const stop=observeEditorFits(read,update=>{
  applies++;
  if(applies===1)queue.push(current=>action('changeRotation',current,{rotation:180}));
  queue.push(update);
 },f.schedule,f.cancel);
 await f.next();state=action('setShownImageDimensions',state,{shownImageDimensions:{width:400,height:300,scaledBy:1}});
 await f.next();await f.next();
 assert.deepEqual(validate(state),[{op:'rotate',degrees:180}]);
 const applied=applies;await f.next();await f.next();assert.equal(applies,applied,'accepted correction must settle without repeated history scaling');stop();
});
test('acknowledged queued correction scales history once despite reducer callback replay',async()=>{
 const f=frames();let state=snapshot(),applies=0;
 state.pastDesignStates=[{adjustments:{crop:{x:80,y:60,width:320,height:240},rotation:0}}];
 const queue=[];const read=async()=>{while(queue.length){const update=queue.shift();assert.deepEqual(providerUpdate(state,update),providerUpdate(state,update));state=providerUpdate(state,update);}return state;};
 const stop=observeEditorFits(read,update=>{applies++;queue.push(update);},f.schedule,f.cancel);
 await f.next();state=action('setShownImageDimensions',state,{shownImageDimensions:{width:400,height:300,scaledBy:1}});await f.next();await f.next();await f.next();
 assert.equal(applies,1);assert.deepEqual(state.pastDesignStates[0].adjustments.crop,{x:50,y:37.5,width:200,height:150});
 const undone=action('undo',state);assert.deepEqual(validate(undone),[{op:'crop',x:80,y:60,width:320,height:240}]);stop();
});
test('null acknowledgement read retains pending basis until a later fenced read',async()=>{
 const f=frames();let state=snapshot(),applies=0,emptyAck=false;
 state.pastDesignStates=[{adjustments:{crop:{x:80,y:60,width:320,height:240},rotation:0}}];
 const read=async()=>{if(emptyAck){emptyAck=false;return null;}return state;};
 const stop=observeEditorFits(read,update=>{applies++;state=providerUpdate(state,update);emptyAck=true;},f.schedule,f.cancel);
 await f.next();state=action('setShownImageDimensions',state,{shownImageDimensions:{width:400,height:300,scaledBy:1}});await f.next();await f.next();await f.next();
 assert.equal(applies,1);assert.deepEqual(state.pastDesignStates[0].adjustments.crop,{x:50,y:37.5,width:200,height:150});stop();
});
