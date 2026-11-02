// Real command/service contracts for component harnesses that mock API methods.
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
module.exports = function peopleFaceRuntime() {
  const cache = new Map();
  function load(relative) {
    const file = path.resolve(__dirname, '../src', relative);
    if (cache.has(file)) return cache.get(file).exports;
    const module = { exports: {} }; cache.set(file, module);
    const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } }).outputText;
    new Function('require', 'module', 'exports', code)(id => id.endsWith('/config') ? { API: '' } : id.startsWith('.') ? load(path.relative(path.resolve(__dirname, '../src'), path.resolve(path.dirname(file), id + '.ts'))) : require(id), module, module.exports);
    return module.exports;
  }
  return { core: load('stores/optimisticOperations.ts'), people: load('services/personActions.ts'), faces: load('services/faceActions.ts'), media: load('services/mediaActions.ts'), tagging: load('services/tagging.ts'), tags: load('services/tagActions.ts') };
};
