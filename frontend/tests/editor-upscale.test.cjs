const assert = require('node:assert/strict');
const { test } = require('node:test');
const { runtime, load, nodes, button, flush, deferred, text } = require('./datasetRuntime.cjs');

function setup(saveCopy, startRepair) {
  const rt=runtime(); const calls=[];
  const {default: Panel}=load('components/EditorUpscalePanel.tsx',rt,{
    '../hooks/useVisiblePolling':{useVisiblePolling(){}},
    '../hotkeys/useHotkey':{useDialogHotkeyScope(){}},
    '../services/repairs':{startRepair:async(...args)=>{calls.push(args);return startRepair ? startRepair(...args) : {id:'job',status:'queued'};}},
    './UpscalerPicker':{__esModule:true,default:'Picker',defaultUpscaleSelection:provider=>({provider:provider||'comfy:remacri-2x',params:{},hostedConsent:false})},
  });
  const props={open:true,media:{id:10},hasChanges:true,initialProvider:null,onProviderChange(){},saveCopy,onClose(){}};
  const render=()=>rt.render(()=>Panel(props));
  let tree=render(); nodes(tree,n=>n.type==='Picker')[0].props.onValidityChange(true); tree=render();
  return{rt,calls,render,tree,props};
}

test('unsaved edits default to saving a copy and queue that copy, never the source',async()=>{
  let saves=0;const s=setup(async()=>{saves++;return{id:20};});
  await button(s.tree,'Start upscale').props.onClick();await flush();
  assert.equal(saves,1);assert.equal(s.calls[0][0],20);s.rt.unmount();
});
test('explicit upscale original skips save; failed save never queues an upscale',async()=>{
  let saves=0;const s=setup(async()=>{saves++;return null;});
  nodes(s.tree,n=>n.type==='RadioGroup')[0].props.onChange({target:{value:'original'}});
  await button(s.render(),'Start upscale').props.onClick();await flush();
  assert.equal(saves,0);assert.equal(s.calls[0][0],10);s.rt.unmount();
  const failure=setup(async()=>null);
  await button(failure.tree,'Start upscale').props.onClick();await flush();
  assert.equal(failure.calls.length,0);failure.rt.unmount();
});


test('changing media or unmounting while save is pending never queues the old image',async()=>{
  for (const unmount of [false,true]) {
    const pending=deferred(); const s=setup(()=>pending.promise);
    button(s.tree,'Start upscale').props.onClick();
    if (unmount) s.rt.unmount();
    else {s.props.media={id:11}; s.render();}
    pending.resolve({id:20}); await flush();
    assert.equal(s.calls.length,0);
    if (!unmount) s.rt.unmount();
  }
});


test('late queued response does not close a new media session or show old progress',async()=>{
  const pending=deferred(); const s=setup(async()=>({id:20}),()=>pending.promise);
  let closes=0;s.props.onClose=()=>{closes++;};
  button(s.render(),'Start upscale').props.onClick(); await flush();
  assert.equal(s.calls.length,1);
  s.props.media={id:11};s.render();
  pending.resolve({id:'old-job',status:'queued'});await flush();
  assert.equal(closes,0);
  assert.doesNotMatch(text(s.render()),/Upscale queued/);
  s.rt.unmount();
});
