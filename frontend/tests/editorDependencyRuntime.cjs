const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'..');
const ts=require(root+'/node_modules/typescript');
const native=require('node:module').createRequire(root+'/node_modules/react-filerobot-image-editor/package.json');
module.exports=function geometry(input){
 let state;
 const stage={attrs:{},children:[{setAttrs(){}},{destroy(){}}],findOne:()=>({cache(){},clearCache(){}}),setAttrs(v){Object.assign(this.attrs,v)},width(){return this.attrs.width},height(){return this.attrs.height},x(){return this.attrs.x},y(){return this.attrs.y},toCanvas(){return{width:Math.floor(this.attrs.width),height:Math.floor(this.attrs.height)}},toDataURL(){return''},destroy(){}};
 state={dispatch(){},originalImage:{width:640,height:480,name:'fixture.jpg'},shownImageDimensions:{width:640,height:480,scaledBy:1},adjustments:{crop:{},rotation:90},resize:{},config:{savingPixelRatio:1,previewPixelRatio:1},...input};
 state.designLayer={attrs:input.clip||{clipX:0,clipY:0,clipWidth:state.shownImageDimensions.width,clipHeight:state.shownImageDimensions.height},setAttr(){},getStage:()=>({clone(attrs){stage.attrs={...attrs};return stage}})};
 function load(file){
  const src=fs.readFileSync(file,'utf8'),m={exports:{}};const code=ts.transpileModule(src,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText;
  function req(id){if(id.endsWith('actions'))return{};if(id==='konva')return{pixelRatio:1};if(id==='./useStore')return()=>state;if(id.endsWith('extractCurrentDesignState'))return()=>({adjustments:state.adjustments,resize:state.resize,finetunes:[],annotations:{}});if(id.endsWith('getFileFullName'))return()=>({name:'fixture',extension:'jpg'});if(id.endsWith('constants'))return{TOOLS_IDS:{CROP:'Crop'},SUPPORTED_IMAGE_TYPES:['jpg']};if(id.endsWith('imageToBase64')||id.endsWith('operationsToCloudimageUrl'))return()=>null;if(id.startsWith('.'))return load(path.resolve(path.dirname(file),id+'.js'));return native(id)}
  new Function('require','module','exports',code)(req,m,m.exports);return m.exports;
 }
 return load(root+'/node_modules/react-filerobot-image-editor/lib/hooks/useTransformedImgData.js').default()({},false,false);
};
if(require.main===module)console.log(module.exports({}).imageData);
