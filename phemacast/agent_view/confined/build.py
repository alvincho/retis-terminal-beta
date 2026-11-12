"""Capture a generic confined runtime; no application scripts or data bundled."""
import base64,hashlib,json,os,subprocess
from pathlib import Path

def build(root,dependencies,destination):
 root=Path(root).resolve();dependencies=Path(dependencies).resolve();destination=Path(destination).resolve()
 if destination.is_relative_to(root):raise ValueError('Runtime capture must remain outside the checkout.')
 source=root/'phemacast/agent_view/confined';node_modules=dependencies/'node_modules'
 wasm=(node_modules/'@jitl/quickjs-wasmfile-debug-sync/dist/emscripten-module.wasm').read_bytes()
 destination.mkdir(parents=True,exist_ok=True,mode=0o700)
 env={**os.environ,'NODE_PATH':str(node_modules)}
 completed=subprocess.run(['node',str(source/'build.mjs'),str(dependencies),str(destination)],cwd=root,env=env,capture_output=True,text=True)
 if completed.returncode:raise RuntimeError('Confined runtime build failed: '+completed.stderr[-3000:])
 files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (destination/'frame.js',)}
 manifest={'schema':'phemacast:confined-runtime/1','version':'1.0.0','engine':'QuickJS/WASM','engineVersion':'0.31.0','files':files,'sourceHashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir() if p.suffix in ('.js','.mjs','.json','.py')},'noBrowserObjectsInVM':True,'networkAPIs':False,'storageAPIs':False,'hostCrossing':'bounded-json-only'}
 (destination/'runtime.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n');return manifest
if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--dependencies',required=True);p.add_argument('--output',required=True);a=p.parse_args();print(json.dumps(build(Path(__file__).resolve().parents[3],a.dependencies,a.output)))
