"""Approved immutable generic hosts, selected independently for each new mount.

Only the local deployment configuration selects code. Phemas and publishers
cannot add a host, runtime, URL, or filesystem path to this registry.
"""
import hashlib,json,os,re,subprocess,tempfile
from pathlib import Path
from prompits.core.capabilities import digest
from .confined_guard import ConfinedRuntime

SCHEMA='phemacast:agent-view-host-release/1'
REFERENCE='phemacast:agent-view-host-release-ref/1'
# Requested by the host's operation feedback relative to host.mjs.
STYLE='owner-busy.css'

def capture(*,source_root,dependencies,runtime_directory,destination):
    root=Path(source_root).resolve();destination=Path(destination).resolve()
    if destination.is_relative_to(root):raise ValueError('Keep captured host releases outside the source checkout.')
    runtime=ConfinedRuntime(runtime_directory)
    if runtime.directory.is_relative_to(root):raise ValueError('Keep the captured runtime outside the source checkout.')
    source=root/'phemacast/agent_view/candidate'
    destination.mkdir(parents=True,exist_ok=True,mode=0o700)
    # The single ESM file has no subsequent imports or application adapters.
    # Its operation feedback links owner-busy.css next to host.mjs, so the
    # release carries that stylesheet with its @import already inlined.
    with tempfile.TemporaryDirectory(dir=destination,prefix='.host-build-') as temporary:
        esbuild=str(Path(dependencies)/'node_modules/.bin/esbuild');inputs=[];built={}
        for name,entry,flags in [('host.mjs',source/'renderer.mjs',['--format=esm']),(STYLE,root/'phemacast/operation_forms_candidate/owner-busy.css',[])]:
            output=Path(temporary)/name;meta=Path(temporary)/(name+'.json')
            subprocess.run([esbuild,str(entry),'--bundle',*flags,'--minify','--outfile='+str(output),'--metafile='+str(meta)],cwd=root,check=True,capture_output=True)
            built[name]=output.read_bytes();inputs+=[(root/path).resolve() for path in json.loads(meta.read_text())['inputs']]
        if any(not any(p.is_relative_to(root/layer) for layer in ('phemacast','prompits')) for p in inputs):raise ValueError('A generic host cannot bundle application code.')
        hashes={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    code=built['host.mjs']
    material={'schema':SCHEMA,'version':'1.0.0','module':{'file':'host.mjs','digest':hashlib.sha256(code).hexdigest(),'bytes':len(code)},
        'styles':{STYLE:{'digest':hashlib.sha256(built[STYLE]).hexdigest(),'bytes':len(built[STYLE])}},
        'runtime':{'digest':runtime.digest},'sourceHashes':hashes}
    identity=digest(material);manifest={**material,'release':identity}
    directory=destination/identity;directory.mkdir(exist_ok=True,mode=0o700)
    for name,data in {**built,'manifest.json':json.dumps(manifest,sort_keys=True,indent=2).encode()}.items():
        path=directory/name
        if path.exists() and path.read_bytes()!=data:raise ValueError('Immutable host release collision.')
        if not path.exists():
            with os.fdopen(os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o400),'wb') as stream:stream.write(data)
    return {'release':identity,'directory':str(directory),'runtimeDirectory':str(runtime.directory)}

class HostReleases:
    def __init__(self,config_path):
        self.config_path=Path(config_path)
        config=self.configuration()
        # Existing unversioned clients continue using this exact runtime.
        self.legacy=ConfinedRuntime(config['runtime_directory'])
        self.runtimes={self.legacy.digest:self.legacy}

    def configuration(self):
        text=self.config_path.read_text()
        if len(text)>512000:raise ValueError('Host configuration exceeds its limit.')
        return json.loads(text)

    def entries(self):
        value=self.configuration().get('host_releases',{})
        if not isinstance(value,dict):raise ValueError('Invalid approved host release registry.')
        rows=value.get('releases',[])
        if not isinstance(rows,list) or len(rows)>64:raise ValueError('Invalid approved host release list.')
        for row in rows:
            if not isinstance(row,dict) or set(row)!={'release','directory','runtimeDirectory'} or not isinstance(row['release'],str) or not re.fullmatch('[a-f0-9]{64}',row['release']):raise ValueError('Invalid approved host release registration.')
            if any(not isinstance(row[key],str) or not Path(row[key]).is_absolute() for key in ('directory','runtimeDirectory')):raise ValueError('Approved host paths must be absolute.')
        ids=[row.get('release') for row in rows]
        if len(ids)!=len(set(ids)):raise ValueError('Approved host releases have duplicate identities.')
        return value.get('default'),rows

    def load(self,identity=None):
        default,rows=self.entries();identity=identity or default
        row=next((r for r in rows if r.get('release')==identity),None)
        if row is None:raise PermissionError('This generic host release has not been approved.')
        directory=Path(row['directory'])
        if not directory.is_absolute():raise ValueError('Approved host release path must be absolute.')
        raw=(directory/'manifest.json').read_bytes()
        if len(raw)>128000:raise ValueError('Host manifest exceeds its limit.')
        manifest=json.loads(raw);material={k:v for k,v in manifest.items() if k!='release'}
        if manifest.get('schema')!=SCHEMA or manifest.get('version')!='1.0.0' or manifest.get('release')!=identity or digest(material)!=identity:
            raise PermissionError('Approved host manifest changed.')
        module=manifest['module']
        if module.get('file')!='host.mjs' or not 1<=module.get('bytes',0)<=1000000:raise ValueError('Invalid host module.')
        code=(directory/'host.mjs').read_bytes()
        if len(code)!=module['bytes'] or hashlib.sha256(code).hexdigest()!=module['digest']:
            raise PermissionError('Approved host code changed.')
        runtime=ConfinedRuntime(row['runtimeDirectory'])
        if runtime.digest!=manifest['runtime']['digest']:raise PermissionError('Host runtime differs from its approved release.')
        existing=self.runtimes.get(runtime.digest)
        if existing and existing.path!=runtime.path:
            # Identical captures may be stored at different deployment paths.
            existing.verify();runtime=existing
        self.runtimes[runtime.digest]=runtime
        return manifest,directory,runtime

    def reference(self):
        manifest,_,_=self.load()
        return {'schema':REFERENCE,'release':manifest['release'],
            'entrypoint':{'path':'/api/agent-view-host-releases/'+manifest['release']+'/host.mjs',
                'digest':manifest['module']['digest'],'bytes':manifest['module']['bytes']},
            'runtime':manifest['runtime']}

    def module(self,identity):
        manifest,directory,_=self.load(identity)
        code=(directory/'host.mjs').read_bytes()
        if len(code)!=manifest['module']['bytes'] or hashlib.sha256(code).hexdigest()!=manifest['module']['digest']:
            raise PermissionError('Approved host code changed.')
        return code

    def style(self,identity,name):
        manifest,directory,_=self.load(identity)
        # Releases captured before styles were carried declare none.
        entry=manifest.get('styles',{}).get(name)
        if not isinstance(entry,dict):raise KeyError('This host release declares no such style.')
        data=(directory/name).read_bytes()
        if len(data)!=entry.get('bytes') or hashlib.sha256(data).hexdigest()!=entry.get('digest'):
            raise PermissionError('Approved host style changed.')
        return data

    def runtime(self,host_release=None):
        if host_release:return self.load(host_release)[2]
        self.legacy.verify();return self.legacy

    def runtime_by_digest(self,identity):
        if not re.fullmatch('[a-f0-9]{64}',identity):raise PermissionError('Invalid runtime identity.')
        # An already mounted view retains its original guard and runtime even
        # when the default changes. No global remount or reload is performed.
        if identity in self.runtimes:
            runtime=self.runtimes[identity];runtime.verify();return runtime
        _,rows=self.entries()
        for row in rows:
            _,_,runtime=self.load(row['release'])
            if runtime.digest==identity:return runtime
        raise PermissionError('Runtime has not been admitted by an approved host release.')
