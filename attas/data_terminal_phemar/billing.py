"""Quote, reserve and settle Plaza scopes; never replay an uncertain execution."""

# OBSOLETE BUSINESS IMPLEMENTATION — TO BE REMOVED AFTER OWNER CUTOVER.
# Owner: plaza_contracts. Do not extend this legacy Terminal implementation.
# See BUSINESS_LOGIC_OBSOLETE.json for replacements and retirement gates.
import time
import secrets
import asyncio
from datetime import datetime, timezone
from fastapi import HTTPException


class ExecutionUncertain(HTTPException):
    stop_fallback = True

    def __init__(self, id):
        super().__init__(409, 'Plaza execution requires reconciliation: ' + id)


def history(store, owner):
    rows=[]
    for saved in store.list(owner,'billing'):
        record=store.decrypt(saved['private'])
        stage=record.get('failedAt')
        rows.append({**{k:v for k,v in saved.items() if k!='private'},'pulse':record.get('pulse'),'recipient':record.get('recipient'),'failedAt':stage,'upstreamStatus':record.get('upstreamStatus'),'canReconcile':record['state']=='uncertain' and stage in ('reserving','reserved','capturing','executing'),'explanation':'Check Plaza for a confirmed provider payment; an unresolved result will not be replayed or refunded.' if record['state']=='uncertain' and stage=='executing' else 'Reservation can be reconciled without replaying the source.' if record['state']=='uncertain' else 'Confirmed Plaza points charge.' if record['state']=='settled' else 'No Plaza points charge.' if record['state']=='refunded' else 'Execution is in progress.'})
    return sorted(rows,key=lambda r:r.get('updatedAt',0),reverse=True)


async def owner_quote(network,agent,pulse_name,parameters,token):
    quote=await network.pulse(agent,pulse_name,parameters,token,practice='quote_pulse_data')
    # Plaza pays the agent's registered owner when it reserves; a provider cannot name the payee in its quote.
    return {key:value for key,value in quote.items() if key not in ('provider_user_id','provider_username')} if isinstance(quote,dict) else quote


class PricedRequest(HTTPException):
    """A free-advertised operation quoted a positive price for this exact request."""
    def __init__(self):
        super().__init__(409, 'This request is priced; it runs only through the paid execution path.')


def budget_ceiling(preferences):
    """The account's daily Plaza-point limit in this Terminal, when one is turned on."""
    preferences = preferences if isinstance(preferences, dict) else {}
    limit = preferences.get('maxPlazaPoints')
    return limit if preferences.get('budgetEnabled') is True and type(limit) is int and limit >= 0 else None


async def execute_free(network, user, agent, pulse_name, parameters):
    """Free controls never reserve funds or wait behind a paid execution."""
    quote = await network.pulse(agent, pulse_name, parameters, user['token'], practice='quote_pulse_data')
    points = quote.get('cost_points', quote.get('points'))
    try:
        expiry = datetime.fromisoformat(quote['valid_until'].replace('Z', '+00:00')).timestamp()
        valid = (quote.get('status') == 'quoted' and type(points) is int and points >= 0
                 and expiry > time.time() and quote.get('currency') == 'plaza_point'
                 and quote.get('agent_id') == agent['id'] and quote.get('pulse_name') == pulse_name)
    except (KeyError, ValueError, TypeError):
        valid = False
    if not valid:
        raise HTTPException(409, 'Connector control requires a current, matching zero-cost quote.')
    if points > 0:
        raise PricedRequest()
    if '_permissionMaxPoints' in user and points > user['_permissionMaxPoints']:
        raise HTTPException(403, 'The quote exceeds this Phema payment grant.')
    result = await network.pulse(agent, pulse_name, parameters, user['token'])
    return {**result, '_billing': {'points': 0, 'state': 'free'}} if isinstance(result, dict) else {'result': result}


async def execute(network, store, user, agent, pulse_name, parameters, ceiling=0):
    quote = await owner_quote(network,agent,pulse_name,parameters,user['token'])
    points = quote.get('cost_points', quote.get('points'))
    try:
        expiry = datetime.fromisoformat(quote['valid_until'].replace('Z','+00:00')).timestamp()
        valid = quote.get('status') == 'quoted' and type(points) is int and points >= 0 and expiry > time.time() and quote.get('currency') == 'plaza_point' and quote.get('agent_id') == agent['id'] and quote.get('pulse_name') == pulse_name
    except (KeyError, ValueError, TypeError):
        valid = False
    if not valid:
        raise HTTPException(409, 'Plaza practice quote is missing, expired or does not match this request.')
    if '_permissionMaxPoints' in user and points>user['_permissionMaxPoints']:
        raise HTTPException(403,'The quote exceeds this Phema payment grant.')
    # Inside a processor's run, every paid step is held and settled together when the run ends (run_escrow.py).
    from .run_escrow import CURRENT as RUN
    run = RUN.get()
    if run is not None:
        run.admit(points)
    if points == 0:
        result=await network.pulse(agent, pulse_name, parameters, user['token'])
        billing={'points':0,'state':'owner-use' if quote.get('ownerUse') else 'free'}
        return {**result,'_billing':billing} if isinstance(result,dict) else {'result':result,'_billing':billing}
    local = store.reserve(user['id'], 'plaza:point', points, ceiling)
    id = secrets.token_hex(16)
    record = {'requestId':'terminal:'+id, 'state':'reserving','points':points,'agentId':agent['id'],'pulse':pulse_name,'localReservation':local,'recipient':'registered owner of '+agent['id']}
    def save():
        store.put(user['id'],'billing',id,{'state':record['state'],'points':points,'agentId':agent['id'],'private':store.encrypt(record)})
    async def post(path, body, authenticated=False):
        response = await network.client.post(network.plaza+path,json=body,headers={'Authorization':'Bearer '+user['token']} if authenticated else {},timeout=40)
        if not response.is_success:
            record['upstreamStatus'] = response.status_code
            try:
                detail = response.json().get('detail')
                record['failureReason'] = detail[:200] if isinstance(detail,str) else 'Plaza rejected the payment payload.'
            except ValueError:
                record['failureReason'] = 'Plaza payment service is unavailable.'
        response.raise_for_status()
        return response.json()
    async def finish(action):
        """Capture or refund a held step when its run ends."""
        record['state'] = 'capturing' if action == 'capture' else 'refunding';save()
        try:
            await post('/api/report-escrows/'+record['escrowId']+'/'+action, {'reservation_token':record['reservationToken'],'request_id':record['requestId']+':'+action,'metadata':{'execution_id':id}}, True)
        except BaseException:
            record['failedAt'] = record['state']
            record['state'] = 'uncertain';save();store.settle(user['id'],local,'uncertain')
            raise ExecutionUncertain(id) from None
        record['state'] = 'settled' if action == 'capture' else 'refunded';save()
        store.settle(user['id'],local,'settled' if action == 'capture' else 'released')
    save()
    try:
        record['reserveBody'] = {'request_id':record['requestId'],'visibility':'private','platform_points':0,'quotes':[quote],'controller_agent_id':network.registration.agent_id,'controller_agent_name':network.registration.agent_name,'controller_owner_user_id':user['id'],'source':'data_terminal','metadata':{'execution_id':id}}
        save()
        reservation = await post('/api/ui_auth/report-generation/reserve', record['reserveBody'], True)
        escrow = reservation.get('escrow') or reservation
        record.update(escrowId=escrow['id'],reservationToken=escrow['reservation_token'],state='reserved');save()
        # Each escrow step carries the signed-in user as well as its reservation token.
        authorization = await post('/api/report-escrows/'+record['escrowId']+'/scopes/authorize', {'reservation_token':record['reservationToken'],'request_id':record['requestId']+':scope','quote':quote,'max_points':points}, True)
        scope = authorization.get('scope') or authorization
        record.update(state='executing',scopeId=scope['id']);save()
        # Inside a run the provider leaves its scope open: Plaza pays every step when the run is captured, none when refunded.
        result = await network.pulse(agent,pulse_name,parameters,user['token'],billing={'scope_id':scope['id'],'scope_token':scope['scope_token'],'quote_id':quote['quote_id'],'pulse_name':pulse_name,
            **({'settlement':'escrow-capture'} if run is not None else {})})
        if run is not None:
            record['state']='held';save()
            run.hold(finish)
            return {**result,'_billing':{'points':points,'state':'held','executionId':id}} if isinstance(result,dict) else {'result':result,'_billing':{'points':points,'state':'held','executionId':id}}
        record['state']='capturing';save()
        await post('/api/report-escrows/'+record['escrowId']+'/capture', {'reservation_token':record['reservationToken'],'request_id':record['requestId']+':capture','metadata':{'execution_id':id}}, True)
        record['state']='settled';save();store.settle(user['id'],local)
        return {**result,'_billing':{'points':points,'state':'settled','executionId':id}} if isinstance(result,dict) else {'result':result,'_billing':{'points':points,'state':'settled','executionId':id}}
    except BaseException as error:
        if run is not None and record.get('escrowId') and record['state'] in ('reserved', 'executing') and not isinstance(error, asyncio.CancelledError):
            # As the report generator does, a run that does not finish is refunded in full when it ends.
            record['failedAt'] = record['state']
            record['state'] = 'refund-pending';save()
            run.hold(finish)
            raise error if isinstance(error, HTTPException) else HTTPException(502, 'The paid step did not complete; its points will be refunded.') from None
        # Keep the same reservation and execution identity after interruption.
        # A provider may already have charged; automatic fetch retries are unsafe.
        record['failedAt'] = record['state']
        record['state']='uncertain';save();store.settle(user['id'],local,'uncertain')
        if isinstance(error, asyncio.CancelledError):
            raise
        raise ExecutionUncertain(id) from None


async def reconcile(network, store, user, id):
    saved = store.get(user['id'],'billing',id)
    record = store.decrypt(saved['private'])
    if record['state'] in ('settled','refunded'):
        return {'id':id,'status':record['state']}
    stage = record.get('failedAt')
    if stage=='executing':
        if not record.get('escrowId') or not record.get('scopeId'):raise HTTPException(409,'Execution evidence is incomplete.')
        response=await network.client.get(network.plaza+'/api/payments/settlements/'+record['escrowId'],headers={'Authorization':'Bearer '+user['token']},timeout=40)
        if not response.is_success:raise HTTPException(409,'Authoritative Plaza settlement status is unavailable; no execution or refund was retried.')
        evidence=response.json()
        scope=next((r for r in evidence.get('scopes',[]) if r.get('id')==record['scopeId']),{})
        if evidence.get('id')!=record['escrowId'] or evidence.get('requestId')!=record['requestId'] or scope.get('provider_agent_id')!=record['agentId'] or scope.get('pulse_name')!=record['pulse'] or scope.get('max_points')!=record['points'] or scope.get('status')!='paid':raise HTTPException(409,'Plaza has not confirmed this exact provider execution as paid. It remains uncertain and will not be replayed or refunded.')
        record['outcomeEvidence']={k:scope.get(k) for k in ('id','status','result_hash','paid_at')}
        stage='capturing'
    if stage not in ('reserving','reserved','capturing'):
        raise HTTPException(409, 'Provider execution is uncertain. Verify the provider outcome before settlement; this action will not replay it.')
    async def post(path, body, authenticated=False):
        response=await network.client.post(network.plaza+path,json=body,headers={'Authorization':'Bearer '+user['token']} if authenticated else {},timeout=40)
        response.raise_for_status()
        return response.json()
    if not record.get('escrowId'):
        if not record.get('reserveBody'):
            raise HTTPException(409, 'Reservation evidence is incomplete; inspect the Plaza ledger.')
        result=await post('/api/ui_auth/report-generation/reserve',record['reserveBody'],True)
        escrow=result.get('escrow') or result
        record.update(escrowId=escrow['id'],reservationToken=escrow['reservation_token'])
    action='capture' if stage=='capturing' else 'refund'
    await post('/api/report-escrows/'+record['escrowId']+'/'+action,{'reservation_token':record['reservationToken'],'request_id':record['requestId']+':'+action},True)
    record['state']='settled' if action=='capture' else 'refunded'
    store.put(user['id'],'billing',id,{'state':record['state'],'points':record['points'],'agentId':record['agentId'],'private':store.encrypt(record)})
    # The uncertainty reservation can now be resolved with confirmed evidence.
    with store.connect() as db:
        db.execute('UPDATE budgets SET status=? WHERE id=? AND owner=?',('settled' if action=='capture' else 'released',record['localReservation'],user['id']))
    return {'id':id,'status':record['state']}
