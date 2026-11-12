"""Loopback-only Terminal control of paired local Phemacast resources."""
from fastapi import HTTPException, Request


def install_local_routes(app, network, principal, name):
    clients = network.local_clients
    @app.get('/api/local-resources')
    async def resources(request: Request):
        user = await principal(request)
        return clients.resources(user['id'])

    @app.post('/api/local-resources/scan')
    async def scan(request: Request):
        user = await principal(request)
        await clients.discovery.rescan()
        return clients.resources(user['id'])

    @app.post('/api/local-resources/pair')
    async def pair(request: Request):
        user = await principal(request)
        body = await request.json()
        return await clients.begin(user['id'], body.get('resourceId'), name)

    @app.post('/api/local-resources/poll')
    async def poll(request: Request):
        user = await principal(request)
        result = await clients.poll(user['id'], (await request.json()).get('id'))
        network.expiry = 0
        return result

    @app.post('/api/local-resources/revoke')
    async def revoke(request: Request):
        user = await principal(request)
        result = await clients.revoke(user['id'], (await request.json()).get('resourceId'))
        network.expiry = 0
        return result

    @app.post('/api/local-resources/call')
    async def call(request: Request):
        user = await principal(request)
        body = await request.json()
        if not isinstance(body.get('parameters'), dict):raise HTTPException(422, 'Provide parameter values.')
        return await clients.call(user['id'], body.get('resourceId'), body.get('operation'), body['parameters'])
