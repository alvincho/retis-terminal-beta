"""This computer's local resources as Terminal settings show them: found by Bonjour, usable at once, no pairing."""
from fastapi import HTTPException, Request


def forget_pairings(store):
    """Remove the pairing records an earlier Terminal kept: local resources no longer pair, so they mean nothing."""
    with store.connect() as db:
        db.execute("DELETE FROM documents WHERE kind='local-resource'")


def install_local_routes(app, network, principal, name, store=None):
    clients = network.local_clients
    if store is not None:
        forget_pairings(store)

    @app.get('/api/local-resources')
    async def resources(request: Request):
        await principal(request)
        return clients.resources()

    @app.post('/api/local-resources/scan')
    async def scan(request: Request):
        await principal(request)
        await clients.discovery.rescan()
        network.expiry = 0
        return clients.resources()

    @app.post('/api/local-resources/call')
    async def call(request: Request):
        await principal(request)
        body = await request.json()
        if not isinstance(body.get('parameters'), dict):raise HTTPException(422, 'Provide parameter values.')
        return await clients.call(body.get('resourceId'), body.get('operation'), body['parameters'])
