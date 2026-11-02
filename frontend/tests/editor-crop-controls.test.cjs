const assert = require('node:assert/strict');
const test = require('node:test');
const { hooks, load } = require('./detailRuntime.cjs');
const math = () => load('utils/editorCrop.ts', hooks());
test('standard ratios include ISO, Letter, current, screen and reject invalid custom ratios', () => {
  const { resolveCropRatio } = math();
  const args = [{width:600,height:400},{width:1920,height:1080}];
  assert.equal(resolveCropRatio('current', ...args), 1.5);
  assert.equal(resolveCropRatio('screen', ...args), 16/9);
  assert.equal(resolveCropRatio('iso-portrait', ...args), 1/Math.SQRT2);
  assert.equal(resolveCropRatio('iso-landscape', ...args), Math.SQRT2);
  assert.equal(resolveCropRatio('letter-portrait', ...args), 8.5/11);
  assert.equal(resolveCropRatio('letter-landscape', ...args), 11/8.5);
  assert.equal(resolveCropRatio('4:5', ...args), .8);
  assert.equal(resolveCropRatio('free', ...args), null);
  for (const bad of ['0:2','4:0','-1:2','NaN','3:2:1']) assert.throws(() => resolveCropRatio(bad, ...args));
});
test('source pixel geometry round-trips display-space crop and clamps bounds', () => {
  const { cropToSource, cropToDisplay, fitCrop, constrainCrop } = math();
  const source={width:1200,height:800}, shown={width:600,height:400};
  const rect={x:100,y:50,width:400,height:300};
  assert.deepEqual(cropToSource(cropToDisplay(rect, source, shown), source, shown),rect);
  assert.deepEqual(constrainCrop({x:1000,y:-20,width:900,height:900}, source),{x:300,y:0,width:900,height:800});
  const fitted=fitCrop(source,4/5);
  assert.equal(fitted.width/fitted.height, .8);
  assert.equal(fitted.x,280);
});
test('upscale and ratio URL state restores and clears all editor-only keys on close', () => {
 const {readEditorOptions,patchEditorSearch}=load('utils/editorUrlState.ts',hooks());
 const params=new URLSearchParams('edit=1&tool=upscale&upscaler=hypir:ultra&ratio=4:5&adv=1&folder=abc');
 const opts=readEditorOptions(params);
 assert.equal(opts.upscale,true);assert.equal(opts.upscaler,'hypir:ultra');assert.equal(opts.ratio,'4:5');assert.equal(opts.advanced,true);
 assert.equal(patchEditorSearch(params,{edit:null}).toString(),'folder=abc');
});
test('theme builder covers actual installed Scaleflex palette values for tools and controls', () => {
 const fs=require('node:fs'), path=require('node:path');
 const native=require('node:module').createRequire(require.resolve('../node_modules/react-filerobot-image-editor'));
 const palettePath=path.join(path.dirname(native.resolve('@scaleflex/ui/utils/types/palette')),'color.js');
 const source=fs.readFileSync(palettePath,'utf8');
 const keys=[...source.matchAll(/:\s*["']([^"']+)["']/g)].map(m=>m[1]);
 assert.ok(keys.includes('txt-primary'));
 for(const mode of ['light','dark']){
  const theme={palette:{text:{primary:mode==='light'?'#111':'#eee',secondary:'#777'},background:{paper:'#fff',default:'#ddd'},primary:{main:'#123',contrastText:'#fff'},warning:{main:'#a60'},error:{main:'#c00'},info:{main:'#06c'},success:{main:'#080'}},typography:{fontFamily:'sans-serif',htmlFontSize:16,fontSize:14}};
  const {buildEditorTheme, buildEditorControlStyles}=load('utils/editorTheme.ts',hooks(),{'@mui/material':{alpha:(c)=>c}});
  const result=buildEditorTheme(theme).palette;
  for(const key of keys)assert.ok(result[key],`missing ${key} in ${mode}`);
  assert.equal(result['txt-primary'],theme.palette.text.primary);
  const styles=buildEditorControlStyles(theme);
  assert.equal(styles['& .FIE_tools-bar-wrapper']['color'],theme.palette.text.primary);
  assert.equal(styles['& .FIE_tools-bar-wrapper input']['WebkitTextFillColor'],theme.palette.text.primary);
 }
});
test('field writes move the display rectangle and preserve rotation; drag reads update source fields', () => {
  const {readCropFields,writeCropFields}=math();
  const image={width:1200,height:800};
  const state={shownImageDimensions:{width:600,height:400},adjustments:{rotation:90,isFlippedX:true,crop:{x:10,y:20,width:100,height:80,ratio:'custom'}}};
  const patch=writeCropFields(state,image,{x:200,y:100,width:400,height:500},.8);
  assert.deepEqual(patch.adjustments.crop,{x:100,y:50,width:200,height:250,ratio:.8});
  assert.equal(patch.adjustments.rotation,90);assert.equal(patch.adjustments.isFlippedX,true);
  assert.equal(patch.isDesignState,true);
  assert.deepEqual(readCropFields({...state,...patch},image),{x:200,y:100,width:400,height:500});
  const dragged={...state,adjustments:{crop:{x:50,y:75,width:200,height:250}}};
  assert.deepEqual(readCropFields(dragged,image),{x:100,y:150,width:400,height:500});
  assert.equal(writeCropFields({},image,{x:0,y:0,width:1,height:1},null),null);
});
test('real crop controls synchronize edited source fields and later dragged rectangles', async () => {
  const {find,settle}=require('./detailRuntime.cjs');
  const rt=hooks(), geometry=math();
  const timers=new Map();let timerId=0;
  const previous=global.window;
  global.window={screen:{width:1920,height:1080},setTimeout:fn=>{timers.set(++timerId,fn);return timerId;},clearTimeout:id=>timers.delete(id)};
  let store={shownImageDimensions:{width:600,height:400},adjustments:{crop:{x:0,y:0,width:300,height:200,ratio:'custom'},rotation:90}};
  const {default:Controls}=load('components/EditorCropControls.tsx',rt,{'../utils/editorCrop':geometry});
  const props={image:{width:1200,height:800},ratio:'free',advanced:true,capture:async()=>store,update:fn=>{const patch=fn(store);if(patch)store={...store,...patch};},onOptions(){},onChanged(){},onFraming(){}};
  const render=()=>rt.render(()=>Controls(props));
  const field=(tree,label)=>find(tree,node=>node.type==='TextField'&&node.props.label===label);
  try {
    render();await settle();let tree=render();
    assert.equal(field(tree,'Width (px)').props.value,'600');
    field(tree,'Width (px)').props.onFocus();
    field(tree,'Width (px)').props.onChange({target:{value:'400'}});
    field(render(),'Width (px)').props.onBlur();
    assert.equal(store.adjustments.crop.width,200);
    assert.equal(store.adjustments.rotation,90);
    store={...store,adjustments:{...store.adjustments,crop:{x:50,y:25,width:250,height:200,ratio:'custom'}}};
    const next=[...timers.values()].at(-1);timers.clear();next();await settle();tree=render();
    assert.equal(field(tree,'Width (px)').props.value,'500');
    assert.equal(field(tree,'X').props.value,'100');
    assert.equal(field(tree,'Y').props.value,'50');
  } finally {rt.unmount();global.window=previous;}
});
test('choosing Free releases a locked crop ratio without replacing its rectangle', async () => {
  const {find,settle}=require('./detailRuntime.cjs');const rt=hooks();const previous=global.window;
  global.window={screen:{width:1920,height:1080},setTimeout:()=>1,clearTimeout(){}};
  let store={shownImageDimensions:{width:600,height:400},adjustments:{crop:{x:0,y:0,width:600,height:400,ratio:'custom'}}};
  const {default:Controls}=load('components/EditorCropControls.tsx',rt,{'../utils/editorCrop':math()});
  const props={image:{width:1200,height:800},ratio:'4:5',advanced:false,capture:async()=>store,update:fn=>{const patch=fn(store);if(patch)store={...store,...patch};},onOptions:options=>{props.ratio=options.ratio;},onChanged(){},onFraming(){}};
  const render=()=>rt.render(()=>Controls(props));
  try {render();await settle();let tree=render();assert.equal(store.adjustments.crop.ratio,.8);
    const before={...store.adjustments.crop};
    find(tree,node=>node.type==='TextField'&&node.props.label==='Aspect ratio').props.onChange({target:{value:'free'}});
    tree=render();
    assert.equal(store.adjustments.crop.ratio,'custom');
    for(const key of ['x','y','width','height'])assert.equal(store.adjustments.crop[key],before[key]);
  }finally{rt.unmount();global.window=previous;}
});
