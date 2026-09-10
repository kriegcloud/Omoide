const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const React = require('react');
function runtime() {
  const slots = []; let cursor = 0; let dirty = false; let effects = [];
  const equal = (a,b) => a && b && a.length === b.length && a.every((x,i) => Object.is(x,b[i]));
  const api = {...React,
    lazy: () => 'LazyEditor',
    useRef(initial) { const i=cursor++; return slots[i] ??= {current:initial}; },
    useState(initial) { const i=cursor++; if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial; return [slots[i], next => { const value = typeof next === 'function' ? next(slots[i]) : next; if (!Object.is(value,slots[i])) { slots[i]=value; dirty=true; } }]; },
    useCallback(fn,deps) { const i=cursor++; if (!slots[i] || !equal(slots[i].deps,deps)) slots[i]={deps,value:fn}; return slots[i].value; },
    useMemo(fn,deps) { const i=cursor++; if (!slots[i] || !equal(slots[i].deps,deps)) slots[i]={deps,value:fn()}; return slots[i].value; },
    useEffect(fn,deps) { const i=cursor++; if (!slots[i] || !equal(slots[i].deps,deps)) effects.push(() => {slots[i]?.cleanup?.(); slots[i]={deps,cleanup:fn()};}); },
  };
  return {api,render(fn) { let result; for (let i=0;i<40;i++) {cursor=0;dirty=false;effects=[];result=fn();effects.forEach(fn=>fn());if(!dirty)return result;} throw Error('Render did not settle');}, unmount(){slots.forEach(x=>x?.cleanup?.());}};
}
const selection = { selectedIds:new Set(),isSelecting:false,clear(){},beginSelecting(){},toggleSelecting(){},setSelected(){} };
function load(relative, rt, overrides={}) {
 const filename=path.resolve(__dirname,'../src',relative);
 const ui=new Proxy({}, {get:(_,name)=>String(name)});
 const mods={react:rt.api,'@mui/material':ui,'react-router-dom':{useParams:()=>({id:'1'}),useNavigate:()=>()=>{},useSearchParams:()=>[new URLSearchParams(),()=>{}]},'react-intersection-observer':{useInView:()=>({inView:false,ref:()=>{}})},'../components/CursorMediaGrid':{CursorMediaGrid:'CursorMediaGrid'},'../components/EmptyState':{EmptyState:'EmptyState'},'../config':{__esModule:true,default:{},API:''},'../context/SelectionContext':{useSelection:()=>selection},'../context/UndoContext':{useUndoRefresh(){},useUndo:()=>({push(){},refreshVisible(){}})},'../hooks/useMarqueeSelection':{useGridSelection:()=>({})},'../hotkeys/useHotkey':{useHotkeys(){}},'../TaskEventsContext':{useTaskCompletionVersion:()=>0},...overrides};
 const js=ts.transpileModule(fs.readFileSync(filename,'utf8'),{fileName:filename,compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText;
 const result={exports:{}};
 const req=name=>{if(name in mods)return mods[name];if(name==='react/jsx-runtime')return require(name);if(name.startsWith('../hooks/useVisiblePolling')||name.startsWith('../utils/dataset'))return load(name.replace('../','')+'.ts',rt,overrides);if(name.includes('/services/'))return {};return {__esModule:true,default:name,...ui};};
 new Function('require','module','exports',js)(req,result,result.exports);return result.exports;
}
function nodes(tree,predicate){const found=[];function visit(node){if(Array.isArray(node)){node.forEach(visit);return;}if(!node||typeof node!=='object')return;if(predicate(node))found.push(node);visit(node.props?.children);}visit(tree);return found;}
const text=node=>Array.isArray(node)?node.map(text).join(''):node&&typeof node==='object'?text(node.props?.children):node??'';
const button=(tree,label)=>nodes(tree,n=>n.type==='Button'&&text(n)===label)[0];
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
module.exports={runtime,load,nodes,text,button,deferred,flush,selection};
