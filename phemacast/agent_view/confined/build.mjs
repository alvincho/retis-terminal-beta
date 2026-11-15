import fs from 'node:fs';import path from 'node:path';import {fileURLToPath} from 'node:url';import {createRequire} from 'node:module';
const source=path.dirname(fileURLToPath(import.meta.url)),deps=process.argv[2],destination=process.argv[3],require=createRequire(path.join(deps,'package.json'));
// The release QuickJS build. A tool folder installed from an older lock has only the debug build, which runs the
// same engine much slower (it records a stack trace on every allocation): build with it rather than fail to start.
const RELEASE='@jitl/quickjs-wasmfile-release-sync',DEBUG='@jitl/quickjs-wasmfile-debug-sync';
const engine=fs.existsSync(path.join(deps,'node_modules',RELEASE,'dist/emscripten-module.wasm'))?RELEASE:DEBUG;
const esbuild=require('esbuild'),wasm=fs.readFileSync(path.join(deps,'node_modules',engine,'dist/emscripten-module.wasm')).toString('base64');
await esbuild.build({entryPoints:[path.join(source,'frame.mjs')],nodePaths:[path.join(deps,'node_modules')],bundle:true,define:{'import.meta.url':JSON.stringify('https://phemacast.invalid/runtime/frame.js')},format:'iife',alias:engine===RELEASE?{}:{[RELEASE]:DEBUG},globalName:'PhemacastConfinedRuntime',minify:true,outfile:path.join(destination,'frame.js'),plugins:[{name:'confined-files',setup(build){build.onResolve({filter:/^phemacast-runtime:/},args=>({path:args.path,namespace:'runtime'}));build.onLoad({filter:/.*/,namespace:'runtime'},args=>({contents:args.path.endsWith(':wasm')?wasm:fs.readFileSync(path.join(source,'environment.js'),'utf8'),loader:'text'}));}}]});
