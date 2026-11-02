const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const ts=require('typescript');
function environment(){
 const cache=new Map();
 function load(relative){
  const file=path.resolve(__dirname,'../src',relative);
  if(cache.has(file))return cache.get(file).exports;
  const module={exports:{}};cache.set(file,module);
  const code=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
  new Function('require','module','exports',code)(id=>id.startsWith('.')?load(path.relative(path.resolve(__dirname,'../src'),path.resolve(path.dirname(file),id+'.ts'))):require(id),module,module.exports);
  return module.exports;
 }
 return {...load('stores/optimisticOperations.ts'),...load('services/personAvatarOptimistic.ts')};
}
test('avatar updates synchronously, definitive failure restores previous face',async()=>{
 const e=environment();let reject;const request=new Promise((_,r)=>{reject=r});
 const base={id:1,profile_face:{id:3},profile_face_id:3};
 const operation=e.startAvatarChange(1,{id:4},()=>request);
 assert.equal(e.projectEntity('person:1',base).profile_face.id,4);
 await new Promise(r=>setImmediate(r));reject(new e.DefinitiveMutationError('rejected'));
 assert.equal((await operation.done).state,'rejected');
 assert.equal(e.projectEntity('person:1',base).profile_face.id,3);
});
test('restoring empty avatar clears both fields',async()=>{
 const e=environment();const operation=e.startAvatarChange(1,null,async()=>{});
 assert.equal(e.projectEntity('person:1',{id:1,profile_face:{id:4}}).profile_face,null);
 assert.equal(e.projectEntity('person:1',{id:1}).profile_face_id,null);
 assert.equal((await operation.done).state,'committed');
});
test('ambiguous network failure remains visible for reconciliation',async()=>{
 const e=environment();const operation=e.startAvatarChange(1,{id:4},async()=>{throw new Error('offline')});
 assert.equal((await operation.done).state,'unknown');
 assert.equal(e.projectEntity('person:1',{id:1}).profile_face.id,4);
});
test('avatar read-only lookup settles an uncertain update without replay',async()=>{
 const e=environment();let calls=0;
 const operation=e.startAvatarChange(1,{id:4},async()=>{calls++;throw new Error('offline')},async id=>({operation_id:id,state:'succeeded',applied:['person:1'],unknown:[],failed:[],skipped:[]}));
 await operation.done;await new Promise(r=>setImmediate(r));
 assert.equal(e.optimisticOperations.getSnapshot().find(record=>record.id===operation.id).state,'committed');
 assert.equal(calls,1);
});
test('auto pick keeps prior photo while pending and commits returned face',async()=>{
 const e=environment();let resolve;const request=new Promise(r=>{resolve=r});
 const operation=e.startAvatarAuto(1,()=>request);
 assert.equal(e.projectEntity('person:1',{profile_face:{id:3}}).profile_face.id,3);
 await new Promise(r=>setImmediate(r));resolve({id:1,profile_face:{id:4}});
 assert.equal((await operation.done).state,'committed');
 assert.equal(e.projectEntity('person:1',{profile_face:{id:3}}).profile_face.id,4);
});
