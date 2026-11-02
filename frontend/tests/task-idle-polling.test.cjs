// Real TS/TSX, controlled hooks and deferred network requests; no DOM runner needed.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const React = require('react');
const noop = () => {};
const tick = async () => { for (let i=0; i<12; i++) await Promise.resolve(); };
const deferred = () => { let resolve, reject; const promise=new Promise((a,b)=>{resolve=a;reject=b;}); return {promise,resolve,reject}; };
function harness() {
  const slots=[]; let cursor=0, dirty=false, effects=[];
  const equal=(a,b)=>a&&b&&a.length===b.length&&a.every((v,i)=>Object.is(v,b[i]));
  const api={...React,
    useRef(value){return slots[cursor++]??={current:value};},
    useState(value){const i=cursor++; if(!(i in slots))slots[i]=typeof value==='function'?value():value; return [slots[i],next=>{const value=typeof next==='function'?next(slots[i]):next;if(!Object.is(value,slots[i])){slots[i]=value;dirty=true;}}];},
    useMemo(fn,deps){const i=cursor++;if(!slots[i]||!equal(slots[i].deps,deps))slots[i]={deps,value:fn()};return slots[i].value;},
    useCallback(fn,deps){return api.useMemo(()=>fn,deps);},
    useEffect(fn,deps){const i=cursor++;if(!slots[i]||!equal(slots[i].deps,deps))effects.push(()=>{slots[i]?.cleanup?.();slots[i]={deps,cleanup:fn()};});},
  };
  return {api,render(fn){for(let n=0;n<20;n++){cursor=0;dirty=false;effects=[];const tree=fn();effects.forEach(f=>f());if(!dirty)return tree;}throw Error('render loop');},unmount(){slots.forEach(s=>s?.cleanup?.());}};
}
function load(file,h,overrides={}) {
  const modules={react:h.api,'./services/task':{getTask:async()=>{throw Error('Unexpected detail request');}},'./stores/mutationBus':{mutationBus:{emit:noop}},...overrides};
  const src=fs.readFileSync(path.resolve(__dirname,'../src',file),'utf8').replaceAll('import.meta.env.DEV','false');
  const code=ts.transpileModule(src,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText;
  const mod={exports:{}};
  new Function('require','module','exports',code)(id=>id in modules?modules[id]:require(id),mod,mod.exports);
  return mod.exports;
}
function contexts(tree){const result=[];function visit(n){if(!n||typeof n!=='object')return;if(n.props?.value)result.push(n.props.value);[n.props?.children].flat(Infinity).forEach(visit);}visit(tree);return result;}
function env(){global.window={addEventListener:noop,removeEventListener:noop,setTimeout,clearTimeout,innerHeight:800,innerWidth:1200,scrollY:0};global.document={visibilityState:'visible',addEventListener:noop,removeEventListener:noop};}
function task(id,status='running'){return {id,status,task_type:'run_processor_for_media'};}
function provider(overrides){env();const h=harness();const {TaskEventsProvider}=load('TaskEventsContext.tsx',h,overrides);const render=()=>h.render(()=>TaskEventsProvider({children:'children'}));const value=()=>contexts(render()).find(v=>v.forceRefresh&&v.activeTasks);return {h,render,value};}

function clock() {
  let id=0; const timers=new Map(),listeners=new Set();
  window.setTimeout=(fn,delay)=>{timers.set(++id,{fn,delay});return id;};
  window.clearTimeout=id=>timers.delete(id);
  document.addEventListener=(name,fn)=>listeners.add(fn);
  document.removeEventListener=(name,fn)=>listeners.delete(fn);
  return {timers,async next(){assert.equal(timers.size,1);const [id,t]=timers.entries().next().value;timers.delete(id);t.fn();await tick();return t.delay;},visibility(state){document.visibilityState=state;document.hidden=state==='hidden';listeners.forEach(fn=>fn());}};
}
test('idle task polling backs off to 30 seconds and active work restores two seconds',async()=>{
  let active=[];const p=provider({'./services/taskActions':{getActiveTasks:async()=>active,getRecentTasks:async()=>[]}}),c=clock();
  const stop=p.value().subscribe();await tick();const delays=[];
  for(let i=0;i<6;i++)delays.push(await c.next());
  assert.deepEqual(delays,[2000,4000,8000,16000,30000,30000]);
  active=[task('new')];await c.next();assert.equal([...c.timers.values()][0].delay,2000);stop();p.h.unmount();
});
test('equal task responses preserve the whole context and nested field changes publish',async()=>{
  let message='same';const p=provider({'./services/taskActions':{getActiveTasks:async()=>[{...task('1'),params:{nested:{message}}}],getRecentTasks:async()=>[task('old','completed')]}});
  await p.value().forceRefresh();const before=p.value();await before.forceRefresh();assert.equal(p.value(),before);
  message='changed';await before.forceRefresh();assert.notEqual(p.value().activeTasks,before.activeTasks);assert.equal(p.value().recentTasks,before.recentTasks);p.h.unmount();
});
test('recent pending-to-terminal transition delivers every completion once through callback',async()=>{
  let recent=[];const delivered=[];env();const h=harness();
  const {TaskEventsProvider}=load('TaskEventsContext.tsx',h,{'./services/taskActions':{getActiveTasks:async()=>[],getRecentTasks:async()=>recent},'./services/task':{getTask:async()=>null}});
  const callback=t=>{delivered.push(t.id);};const value=()=>contexts(h.render(()=>TaskEventsProvider({children:null,onTaskFinished:callback}))).find(v=>v.forceRefresh);
  await value().forceRefresh();recent=[task('a','pending'),task('b','pending')];await value().forceRefresh();recent=[task('a','completed'),task('b','failed')];await value().forceRefresh();await value().forceRefresh();
  assert.deepEqual(delivered,['a','b']);assert.equal(value().globalCompletionCount,1);h.unmount();
});
test('visibility restoration during a request leaves exactly one polling timer',async()=>{
  const pending=deferred();const p=provider({'./services/taskActions':{getActiveTasks:()=>pending.promise,getRecentTasks:async()=>[]}}),c=clock();
  const stop=p.value().subscribe();c.visibility('hidden');c.visibility('visible');pending.resolve([]);await tick();assert.equal(c.timers.size,1);stop();p.h.unmount();
});
test('visible polling does not schedule duplicate timers when visibility changes in flight',async()=>{
  env();const c=clock(),h=harness(),pending=deferred();let calls=0;
  const {useVisiblePolling}=load('hooks/useVisiblePolling.ts',h);const request=()=>{calls++;return pending.promise;};
  h.render(()=>useVisiblePolling(request,2000,true,true));c.visibility('hidden');c.visibility('visible');pending.resolve();await tick();assert.equal(c.timers.size,1);await c.next();assert.equal(calls,2);h.unmount();
});
test('initial historical completions do not replay after leaving and reentering recent history',async()=>{
  let recent=[task('old','completed')];const p=provider({'./services/taskActions':{getActiveTasks:async()=>[],getRecentTasks:async()=>recent}});
  await p.value().forceRefresh();recent=[];await p.value().forceRefresh();recent=[task('old','completed')];await p.value().forceRefresh();assert.equal(p.value().globalCompletionCount,0);p.h.unmount();
});
test('manual refresh joined by a subscription shares the request and leaves one timer',async()=>{
  const pending=deferred();let calls=0;const p=provider({'./services/taskActions':{getActiveTasks:()=>{calls++;return pending.promise;},getRecentTasks:async()=>[]}}),c=clock();
  const refresh=p.value().forceRefresh();const stop=p.value().subscribe();pending.resolve([]);await refresh;await tick();assert.equal(calls,1);assert.equal(c.timers.size,1);stop();p.h.unmount();
});
test('latest terminal recent snapshot wins over the stale disappeared active snapshot',async()=>{
  let active=[task('a')],recent=[task('a','pending')],details=0;
  const p=provider({'./services/taskActions':{getActiveTasks:async()=>active,getRecentTasks:async()=>recent},'./services/task':{getTask:async()=>{details++;return null;}}});
  await p.value().forceRefresh();active=[];recent=[task('a','completed')];await p.value().forceRefresh();assert.equal(p.value().globalCompletionCount,1);assert.equal(details,0);p.h.unmount();
});
