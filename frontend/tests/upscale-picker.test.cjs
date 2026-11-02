const assert = require('node:assert/strict');
const { test } = require('node:test');
const { runtime, load, nodes, text, flush } = require('./datasetRuntime.cjs');

const providers = [
  {id:'comfy:remacri-2x',label:'Remacri',kind:'local',fidelity_note:'Faithful',available:true,params:{scale:{type:'integer',default:2,choices:[2]}}},
  {id:'hypir:ultra',label:'HYPIR Ultra',kind:'hosted',fidelity_note:'Generative',available:true,params:{scale:{type:'integer',default:2,min:1,max:4},prompt:{type:'string',default:''},long_edge_size:{type:'integer',default:-1,min:-1,max:16384},short_edge_size:{type:'integer',default:-1,min:-1,max:16384}}},
  {id:'hypir:balance',label:'HYPIR Balance',kind:'hosted',fidelity_note:'Generative',available:false,reason:'API key not configured',params:{}},
];

test('hosted picker requires per-run consent, shows disabled reasons and honors numeric ranges', async () => {
  const rt = runtime();
  const {default: Picker} = load('components/UpscalerPicker.tsx',rt,{'../services/repairs':{listUpscalers:async()=>providers}});
  let value = {provider:'hypir:ultra',params:{},hostedConsent:false};
  let valid;
  const render = () => rt.render(() => Picker({value,onChange:next=>{value=next;},onValidityChange:next=>{valid=next;}}));
  render(); await flush(); let tree=render();
  assert.equal(valid,false);
  assert.match(nodes(tree,n=>n.type==='FormControlLabel')[0].props.label,/Send this image to the hosted service for this run/);
  const disabled=nodes(tree,n=>n.type==='MenuItem'&&n.props.value==='hypir:balance')[0];
  assert.equal(disabled.props.disabled,true);
  assert.match(text(disabled),/API key not configured/);
  nodes(tree,n=>n.type==='FormControlLabel')[0].props.control.props.onChange({target:{checked:true}});
  tree=render(); assert.equal(valid,true);
  nodes(tree,n=>n.type==='TextField'&&n.props.label==='Scale')[0].props.onChange({target:{value:'9'}});
  tree=render(); assert.equal(valid,false);
  nodes(tree,n=>n.type==='Select')[0].props.onChange({target:{value:'comfy:remacri-2x'}});
  render(); assert.equal(value.hostedConsent,false); assert.equal(valid,true);
  rt.unmount();
});


test('HYPIR edge size zero is invalid and precedence is explained; unknown provider is explicit',async()=>{
  const rt=runtime();
  const {default:Picker}=load('components/UpscalerPicker.tsx',rt,{'../services/repairs':{listUpscalers:async()=>providers}});
  let value={provider:'hypir:ultra',params:{long_edge_size:0},hostedConsent:true}; let valid;
  const render=()=>rt.render(()=>Picker({value,onChange:next=>{value=next;},onValidityChange:next=>{valid=next;}}));
  render();await flush(); let tree=render();
  assert.equal(valid,false);
  value={...value,params:{long_edge_size:2048,short_edge_size:1024}};tree=render();
  assert.match(text(tree),/Long edge overrides short edge; either overrides scale/);
  assert.equal(valid,true);
  value={...value,provider:'unknown:provider'};tree=render();
  assert.equal(valid,false);assert.match(text(tree),/Unknown upscaler/);
  rt.unmount();
});
