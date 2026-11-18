"""Owner installation of model bundles. Users' vendor keys never reach this source."""
import httpx

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool


def install_model_routes(source):
    @source.app.post('/api/data/models/install')
    async def install(request: Request):
        await source._authorize_shared_agent_settings_write(request)
        token = request.headers.get('authorization', '').removeprefix('Bearer ').strip()
        if len(await request.body()) > 512_000:
            raise HTTPException(413, 'Model installation exceeds 512 KB.')
        try:
            body = await request.json()
            chain = body.get('chain', [])
            if not isinstance(chain, list) or not 1 <= len(chain) <= 3:
                raise ValueError('Provide one to three exact mapping references.')
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(source.plaza_url.rstrip('/') + '/api/schema-query',
                    json={'refs': chain}, headers={'Authorization': 'Bearer ' + token})
                if not response.is_success:
                    raise HTTPException(response.status_code, 'The authorized dependency bundle is unavailable.')
            from .installed import install as install_bundle
            result = await run_in_threadpool(install_bundle, source, response.json()['records'], chain,
                                           body.get('vectors'), source._configuration_owner_id())
            source._apply_source_card()
            return result
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(422, str(exc)) from exc
