"""Runtime-bound guard for the installed JSON-only QuickJS drawing host.

This never attests arbitrary browser execution or a fixture interception rule.
Only the reviewed confined runtime path may use this guard; the old iframe-eval
host has no production guard and remains unsuitable for confidential data.
"""
import hashlib,json,time
from pathlib import Path

class ConfinedRuntime:
    def __init__(self,directory):
        self.directory=Path(directory).resolve()
        self.manifest=json.loads((self.directory/'runtime.json').read_text())
        if (self.manifest.get('schema')!='phemacast:confined-runtime/1' or self.manifest.get('engine')!='QuickJS/WASM'
                or self.manifest.get('noBrowserObjectsInVM') is not True or self.manifest.get('networkAPIs') is not False
                or self.manifest.get('storageAPIs') is not False):raise ValueError('A reviewed confined runtime is required.')
        self.path=self.directory/'frame.js';self.digest=self.manifest['files']['frame.js'];self.verify()
    def verify(self):
        self.read()
    def read(self):
        value=self.path.read_bytes()
        if hashlib.sha256(value).hexdigest()!=self.digest:raise PermissionError('Confined runtime changed; refuse active mounting.')
        return value
    def reference(self):
        self.verify();return {'type':'quickjs-wasm','digest':self.digest,'path':'/api/agent-view-runtime/'+self.digest+'/frame.js'}
    def guard(self,*,actor,mount_id,manifest_digest,publisher_origin,expires_at):
        self.verify();return ConfinedRuntimeGuard(self,actor,mount_id,manifest_digest,publisher_origin,expires_at)

class ConfinedRuntimeGuard:
    def __init__(self,runtime,actor,mount,manifest,origin,expiry):
        self.runtime=runtime;self.scope=(actor,mount,manifest,origin);self.expiry=expiry;self.closed=False
    def verify_scope(self,*,actor,mount_id,manifest_digest,publisher_origin):
        if self.closed or time.time()>=self.expiry or (actor,mount_id,manifest_digest,publisher_origin)!=self.scope:return False
        self.runtime.verify();return True
    def close(self):self.closed=True
