const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const React = require('react');
function hooks() {
  const slots = []; let cursor = 0; let dirty = false; let effects = [];
  const same = (a,b) => a && b && a.length === b.length && a.every((v,i) => Object.is(v,b[i]));
  const api = { ...React,
    useRef(value) { return slots[cursor++] ??= { current: value }; },
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial;
      return [slots[i], value => { const next = typeof value === 'function' ? value(slots[i]) : value; if (!Object.is(next, slots[i])) { slots[i] = next; dirty = true; } }]; },
    useCallback(fn,deps) { const i=cursor++; if (!slots[i] || !same(slots[i].deps,deps)) slots[i]={deps,value:fn}; return slots[i].value; },
    useMemo(fn,deps) { const i=cursor++; if (!slots[i] || !same(slots[i].deps,deps)) slots[i]={deps,value:fn()}; return slots[i].value; },
    useEffect(fn,deps) { const i=cursor++; if (!slots[i] || !same(slots[i].deps,deps)) effects.push(() => { slots[i]?.cleanup?.(); slots[i]={deps,cleanup:fn()}; }); },
  };
  api.useLayoutEffect = api.useEffect;
  return { api, render(fn) { for(let n=0;n<30;n++) { cursor=0; dirty=false; effects=[]; const value=fn(); effects.forEach(fn=>fn()); if(!dirty) return value; } throw Error('Render did not settle'); }, unmount() { slots.forEach(slot=>slot?.cleanup?.()); } };
}
const noop = () => {};
const asyncNoop = async () => {};
function load(relative, runtime, modules={}) {
  const filename=path.resolve(__dirname,'../src',relative);
  const ui=new Proxy({useTheme:()=>({breakpoints:{down:noop}}),useMediaQuery:()=>false},{get:(target,key)=>target[key]??key});
  const defaults={react:runtime.api,'@mui/material':ui,'@mui/lab':ui,
    '../config':{__esModule:true,default:{PERSON_RELATIONSHIP_MAX_NODES:100}, API:'/api'},
    '../context/UndoContext':{useUndo:()=>({push:noop,refreshVisible:asyncNoop}),useUndoRefresh:noop,useMutationRefresh:noop},
    '../hotkeys/useHotkey':{useDialogHotkeyScope:()=>({current:null}),useHotkey:noop,useHotkeyHelp:()=>noop},
    '../hotkeys/keymap':{getTopModal:()=>null},
    '../TaskEventsContext':{useTaskCompletionVersion:()=>0},
    '../stores/mutationBus':{mutationBus:{emit:noop,subscribe:()=>noop},runOptimistic:async ({apply,request,rollback})=>{const snapshot=apply();try{return await request();}catch(err){rollback(snapshot);throw err;}}},
    '../services/config':{getConfig:async()=>({general:{}})},
    '../services/repairs':{listRepairs:async()=>({items:[]})},
    '../stores/peopleCache':{patchPersonInGrids:noop,clearPeopleGrids:noop,removePeopleFromGrids:noop},
    'react-intersection-observer':{useInView:()=>({ref:noop,inView:false})},
    ...modules,
  };
  const source=fs.readFileSync(filename,'utf8').replace(/import\.meta\.env\.DEV/g,'false');
  const output=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true},fileName:filename}).outputText;
  const module={exports:{}};
  new Function('require','module','exports',output)(name=>{
    if(name in defaults)return defaults[name];
    if(name==='react/jsx-runtime')return require(name);
    if(name.startsWith('@mui/'))return {__esModule:true,default:name};
    return new Proxy({__esModule:true,default:name},{get:(target,key)=>target[key]??key});
  },module,module.exports);
  return module.exports;
}
function find(node,match) {if(!node||typeof node!=='object')return; if(match(node))return node; for(const child of [node.props?.children].flat(Infinity)){const found=find(child,match);if(found)return found;} }
function component(node,name) {return find(node,node=>node.type===name);}
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
async function settle(){for(let i=0;i<8;i++)await Promise.resolve();}
function listStore(items=[]) {
  const state={lists:{images:{items:[...items],hasMore:false,isLoading:false}},
    clearList:noop,fetchInitial:asyncNoop,loadMore:asyncNoop,
    removeItem:(key,id)=>{state.lists[key].items=state.lists[key].items.filter(item=>item.id!==id);},
    removeItems:noop,updateItem:noop};
  const useListStore=selector=>selector?selector(state):state;useListStore.getState=()=>state;
  return {state,module:{useListStore,useListInvalidation:noop,defaultListState:{items:[],hasMore:false,isLoading:false},refreshCachedList:asyncNoop}};
}
module.exports={hooks,load,find,component,deferred,settle,listStore,noop};
