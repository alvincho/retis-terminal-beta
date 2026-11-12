"""Uninstalled generic adapter for the existing content request channel."""
from .pulse_forms import CAPABILITY

OPERATIONS={'prepare-interaction','invoke-interaction','observe-interaction'}

async def handle_form_operation(session,body):
    if body.get('operation') not in OPERATIONS:
        raise ValueError('Unknown protected content operation.')
    refs=body.get('references')
    if not isinstance(refs,list) or len(refs)!=1 or not isinstance(refs[0],str):
        raise ValueError('Interact with one selected mounted reference.')
    if body['operation']=='prepare-interaction':
        if set(body)!={'operation','references','inputs','requestKey'}:
            raise ValueError('Unexpected preparation fields.')
        return await session.prepare({'reference':refs[0],'inputs':body['inputs'],'requestKey':body['requestKey']})
    if set(body)!={'operation','references','requestKey','wireDigest'}:
        raise ValueError('Unexpected invocation/status fields.')
    if body['operation']=='invoke-interaction':
        return await session.invoke({'reference':refs[0],'requestKey':body['requestKey'],'wireDigest':body['wireDigest']})
    return await session.observe({'reference':refs[0],'intentId':body['requestKey'],'wireDigest':body['wireDigest']})

def transport_marker():
    # Emitted by an installed, actor-bound host session only, never the Phema.
    return dict(CAPABILITY)
