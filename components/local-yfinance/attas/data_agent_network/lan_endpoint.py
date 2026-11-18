"""Optional TLS listener for a source's data API on a private local network."""
import argparse
import asyncio
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import ssl

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
import uvicorn


def endpoint_descriptor(config):
    address=ipaddress.ip_address(config['address'])
    ranges=('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16') if address.version==4 else ('fc00::/7',)
    if not any(address in ipaddress.ip_network(n) for n in ranges) or not 1024<=int(config['port'])<=65535:
        raise ValueError('LAN endpoints require a private IP and a dedicated unprivileged port.')
    pem=Path(config['certificate']).read_text()
    fingerprint=hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
    host='['+str(address)+']' if address.version==6 else str(address)
    return {'url':f"https://{host}:{int(config['port'])}",'tlsFingerprintSha256':fingerprint}


def source_endpoint_app(origin):
    app=FastAPI(docs_url=None,openapi_url=None)
    @app.api_route('/{path:path}',methods=['GET','HEAD','POST'])
    async def proxy(path:str,request:Request):
        if not re.fullmatch(r'[A-Za-z0-9_./-]+',path) or any(p in ('.','..') for p in path.split('/')):
            raise HTTPException(404)
        read=request.method in ('GET','HEAD')
        allowed=(read and (path=='health' or re.fullmatch(r'api/streams/[a-z0-9_-]+/[a-z0-9_-]+',path))) or (request.method=='POST' and path in ('use_practice/get_pulse_data','use_practice/quote_pulse_data','api/data/models/fetch','api/data/fugle/fetch'))
        if not allowed:raise HTTPException(403,'Only the source data API is available on this listener.')
        payload=b''
        async for chunk in request.stream():
            payload+=chunk
            if len(payload)>1_000_000:raise HTTPException(413)
        if path.startswith('use_practice/'):
            try:name=json.loads(payload).get('content',{}).get('pulse_name')
            except (ValueError,AttributeError):raise HTTPException(422)
            if name not in ('data_spec','data_availability','schema_query','data_fetch','pulse_chat'):
                raise HTTPException(403,'This source operation is not exposed on the LAN.')
        drop={'host','connection','cookie','content-length','transfer-encoding','forwarded','x-forwarded-for','x-forwarded-host','x-forwarded-proto'}
        headers={k:v for k,v in request.headers.items() if k.lower() not in drop}
        client=httpx.AsyncClient(trust_env=False,follow_redirects=False,timeout=httpx.Timeout(120,connect=3))
        try:
            upstream=await client.send(client.build_request(request.method,origin+'/'+path+('?' + request.url.query if request.url.query else ''),headers=headers,content=payload or None),stream=True)
            if 300<=upstream.status_code<400:
                await upstream.aclose();await client.aclose()
                raise HTTPException(502,'Source redirect rejected.')
        except BaseException:
            await client.aclose();raise
        async def stream():
            try:
                async for chunk in upstream.aiter_raw():yield chunk
            finally:
                await upstream.aclose();await client.aclose()
        return StreamingResponse(stream(),status_code=upstream.status_code,headers={k:v for k,v in upstream.headers.items() if k.lower() not in drop|{'set-cookie'} })
    return app


def install_lan_endpoint(source):
    config=source._config_data.get('lan_endpoint')
    if not config:return
    source._lan_descriptor=endpoint_descriptor(config)
    # The source's existing Pulse/user authorization remains the enforcement
    # point. The TLS listener exposes no configuration or lifecycle routes.
    server=uvicorn.Server(uvicorn.Config(source_endpoint_app(f'http://127.0.0.1:{source.port}'),host=config['address'],port=int(config['port']),
        ssl_certfile=config['certificate'],ssl_keyfile=config['private_key'],log_level='warning',access_log=False))
    server.install_signal_handlers=lambda:None
    server.capture_signals=nullcontext
    task=None
    @source.app.on_event('startup')
    async def start():
        nonlocal task
        task=asyncio.create_task(server.serve())
        for _ in range(50):
            if server.started:return
            if task.done():task.result();raise RuntimeError('LAN listener failed to start.')
            await asyncio.sleep(.1)
        raise RuntimeError('LAN listener startup timed out.')
    @source.app.on_event('shutdown')
    async def stop():
        server.should_exit=True
        if task:await task


def configure(path,address,port):
    """Owner-operated setup; creates a source-specific certificate outside code."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    document=json.loads(path.read_text())
    folder=path.parent/'lan-tls';folder.mkdir(mode=0o700,exist_ok=True)
    cert,keyfile=folder/'source.crt',folder/'source.key'
    if cert.exists() or keyfile.exists():raise ValueError('Certificate already exists. Keep its published identity or rotate explicitly.')
    ip=ipaddress.ip_address(address)
    private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Source local API')])
    now=datetime.now(timezone.utc)
    certificate=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(private.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip)]),critical=False).sign(private,hashes.SHA256()))
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));keyfile.chmod(0o600)
    config={'address':address,'port':port,'certificate':str(cert.resolve()),'private_key':str(keyfile.resolve())}
    descriptor=endpoint_descriptor(config)
    document['lan_endpoint']=config
    path.write_text(json.dumps(document,indent=2)+'\n');path.chmod(0o600)
    return descriptor


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--address',required=True)
    parser.add_argument('--port',type=int,required=True)
    args=parser.parse_args()
    print(json.dumps(configure(args.config,args.address,args.port)))
