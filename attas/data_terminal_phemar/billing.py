"""Quote, reserve and settle Plaza scopes; never replay an uncertain execution."""

# OBSOLETE BUSINESS IMPLEMENTATION — TO BE REMOVED AFTER OWNER CUTOVER.
# Owner: plaza_contracts. Do not extend this legacy Terminal implementation.
# See BUSINESS_LOGIC_OBSOLETE.json for replacements and retirement gates.
import time
import secrets
import asyncio
from datetime import datetime, timezone
import httpx
from fastapi import HTTPException

# A provider that leaves payment to the escrow holds its result until Plaza confirms the delivery. Until then it answers
# 503 (or 409 while the same scope is still running) with Retry-After: the same scope is sent again, a few times and
# within a bounded wait, before Plaza is asked what happened to it. A new reservation is never made for the same step.
PENDING_ATTEMPTS = 4
PENDING_WAIT_CAP = 10.0
PENDING_TOTAL_WAIT = 30.0
# The provider's definite answer that the scope's work already ran and its result is lost (it never runs it again).
RESULT_LOST_HEADER = 'x-payment-scope-outcome'
RESULT_LOST = 'executed-result-lost'


class ExecutionUncertain(HTTPException):
    stop_fallback = True

    def __init__(self, id):
        super().__init__(409, 'Plaza execution requires reconciliation: ' + id)


def history(store, owner):
    rows=[]
    for saved in store.list(owner,'billing'):
        record=store.decrypt(saved['private'])
        stage=record.get('failedAt')
        settled=record.get('settledPoints')
        # Points are what Plaza charged. A capture that paid no provider returned the whole reservation, as a refund does.
        returned=record['state']=='settled' and settled==0
        # A refund that still paid a provider for work Plaza recorded as delivered is a charge.
        paid=record['state']=='refunded' and bool(settled)
        shown={'state':'refunded','points':0} if returned else {'state':'settled','points':settled} if paid else {'points':settled or 0} if record['state']=='refunded' else {'points':settled} if record['state']=='settled' and settled is not None else {}
        rows.append({**{k:v for k,v in saved.items() if k!='private'},**shown,'pulse':record.get('pulse'),'recipient':record.get('recipient'),'failedAt':stage,'upstreamStatus':record.get('upstreamStatus'),'settledPoints':settled,'canReconcile':record['state']=='uncertain' and stage in ('reserving','reserved','capturing','refunding','executing'),'explanation':'Check Plaza for a confirmed provider payment; an unresolved result will not be replayed or refunded.' if record['state']=='uncertain' and stage=='executing' else 'Reservation can be reconciled without replaying the source.' if record['state']=='uncertain' else 'No Plaza points charge: Plaza paid no provider and returned the reserved points.' if returned else 'Confirmed Plaza points charge.' if record['state']=='settled' else f'Charged: the step stopped, and Plaza paid its provider {settled} points for work it recorded as delivered'+('; the rest was returned.' if settled<record['points'] else '.') if paid else 'No Plaza points charge.' if record['state']=='refunded' else 'Execution is in progress.'})
    return sorted(rows,key=lambda r:r.get('updatedAt',0),reverse=True)


async def owner_quote(network,agent,pulse_name,parameters,token):
    quote=await network.pulse(agent,pulse_name,parameters,token,practice='quote_pulse_data')
    # Plaza pays the agent's registered owner when it reserves; a provider cannot name the payee in its quote.
    return {key:value for key,value in quote.items() if key not in ('provider_user_id','provider_username')} if isinstance(quote,dict) else quote


def pending_wait(error):
    """Seconds a provider asked to wait before the same payment scope is sent again; None when its answer is final."""
    if not isinstance(error, httpx.HTTPStatusError) or error.response.status_code not in (409, 503):
        return None
    headers = error.response.headers
    if headers.get(RESULT_LOST_HEADER) or headers.get('retry-after') is None:
        return None
    try:
        seconds = float(headers['retry-after'])
    except ValueError:
        seconds = PENDING_WAIT_CAP  # An HTTP date: wait the longest single wait.
    return min(max(seconds, 0.0), PENDING_WAIT_CAP)


def result_lost(error):
    """The provider's definite answer: the scope's work already ran, its result is lost, and it will not run again."""
    return (isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 409
            and error.response.headers.get(RESULT_LOST_HEADER) == RESULT_LOST)


def delivered_points(answer, scope_id, points):
    """Points a refund still paid this step's provider, for work Plaza recorded as delivered (none when it names none)."""
    escrow = answer.get('escrow') if isinstance(answer, dict) and isinstance(answer.get('escrow'), dict) else {}
    metadata = escrow.get('metadata') if isinstance(escrow.get('metadata'), dict) else {}
    paid = sum(item['points'] for item in metadata.get('delivered_settlements') or []
               if isinstance(item, dict) and item.get('scope_id') == scope_id and type(item.get('points')) is int and item['points'] > 0)
    return min(paid, points)


def captured_points(answer, points):
    """Points a capture actually charged this step: what Plaza paid its provider. Plaza hands the rest of the
    reservation back, so a capture that paid no provider (none it could resolve, or the account's own agent) costs nothing.
    An answer that names no settlements keeps the quoted points."""
    escrow = answer.get('escrow') if isinstance(answer, dict) and isinstance(answer.get('escrow'), dict) else {}
    metadata = escrow.get('metadata') if isinstance(escrow.get('metadata'), dict) else {}
    settlements = answer.get('settlements') if isinstance(answer, dict) and isinstance(answer.get('settlements'), list) else metadata.get('settlements')
    if not isinstance(settlements, list):
        return points
    paid = sum(item['points'] for item in settlements
               if isinstance(item, dict) and item.get('status') in ('settled', 'already_paid_scope') and type(item.get('points')) is int and item['points'] > 0)
    return min(paid, points)


async def scope_status(network, user, record):
    """This step's scope status as Plaza records it ('authorized', 'running', 'completed', 'paid', ...), or None."""
    try:
        response = await network.client.get(network.plaza+'/api/payments/settlements/'+record['escrowId'],headers={'Authorization':'Bearer '+user['token']},timeout=40)
        evidence = response.json() if response.is_success else {}
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(evidence, dict) or evidence.get('id') != record['escrowId'] or evidence.get('requestId') != record['requestId']:
        return None
    scope = next((r for r in evidence.get('scopes') or [] if isinstance(r, dict) and r.get('id') == record.get('scopeId')), {})
    if scope.get('provider_agent_id') != record['agentId'] or scope.get('max_points') != record['points']:
        return None
    return scope.get('status') or None


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
        """Capture or refund a held step when its run ends; a refund records what Plaza still paid for delivered work."""
        record['state'] = 'capturing' if action == 'capture' else 'refunding';save()
        try:
            answer = await post('/api/report-escrows/'+record['escrowId']+'/'+action, {'reservation_token':record['reservationToken'],'request_id':record['requestId']+':'+action,'metadata':{'execution_id':id}}, True)
        except BaseException:
            record['failedAt'] = record['state']
            record['state'] = 'uncertain';save();store.settle(user['id'],local,'uncertain')
            raise ExecutionUncertain(id) from None
        if action == 'capture':
            charged = captured_points(answer, points)
            record.update(state='settled', settledPoints=charged);save()
            store.settle_part(user['id'],local,charged)
            return charged
        settled = delivered_points(answer, record.get('scopeId'), points)
        record.update(state='refunded', settledPoints=settled);save()
        store.settle_part(user['id'],local,settled)
        return settled
    async def call_provider():
        """Send the step under its one scope; while the provider waits on Plaza's delivery record, send the same scope again."""
        waited = 0.0
        for attempt in range(PENDING_ATTEMPTS):
            try:
                return await network.pulse(agent,pulse_name,parameters,user['token'],billing=record['providerBilling'])
            except httpx.HTTPStatusError as error:
                wait = pending_wait(error)
                if wait is None or attempt == PENDING_ATTEMPTS - 1 or waited + wait > PENDING_TOTAL_WAIT:
                    raise
                record['pendingAcknowledgements'] = attempt + 1;save()
                await asyncio.sleep(wait)
                waited += wait
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
        # Inside a run the provider leaves its scope open: Plaza pays every step when the run is captured, and only the
        # steps it recorded as delivered when the run is refunded.
        record.update(state='executing',scopeId=scope['id'],providerBilling={'scope_id':scope['id'],'scope_token':scope['scope_token'],'quote_id':quote['quote_id'],'pulse_name':pulse_name,
            **({'settlement':'escrow-capture'} if run is not None else {})});save()
        try:
            result = await call_provider()
        except httpx.HTTPStatusError as error:
            pending, lost = pending_wait(error) is not None, result_lost(error)
            if not (pending or lost):
                raise
            # The provider ran this scope (or still holds its result) but cannot hand it back now. Ask Plaza before
            # anything is refunded; a scope Plaza recorded as delivered is sent once more for its held result.
            record['resultPending' if pending else 'resultLost'] = True
            status = await scope_status(network, user, record)
            record['scopeStatus'] = status;save()
            result = None
            if pending and status == 'completed':
                try:
                    result = await network.pulse(agent,pulse_name,parameters,user['token'],billing=record['providerBilling'])
                except httpx.HTTPStatusError:
                    result = None
            if result is None:
                if run is not None:
                    raise HTTPException(502, 'The paid step\'s result could not be confirmed with Plaza; its points will be refunded, less work Plaza recorded as delivered.') from None
                if status is None:
                    raise
                # Outside a run this step is its own escrow: settle it now, with what Plaza says was delivered.
                record['failedAt'] = 'executing'
                if status == 'paid':
                    await finish('capture')
                    raise HTTPException(502, f'The provider was paid {points} points but could not hand back its result; it will not run again.') from None
                settled = await finish('refund')
                raise HTTPException(502, (f'The provider could not hand back its result. {settled} points were paid for work Plaza recorded as delivered and {points - settled} refunded.'
                                          if settled else f'The provider could not hand back its result; all {points} points were refunded.')) from None
        if run is not None:
            record['state']='held';save()
            run.hold(finish)
            return {**result,'_billing':{'points':points,'state':'held','executionId':id}} if isinstance(result,dict) else {'result':result,'_billing':{'points':points,'state':'held','executionId':id}}
        record['state']='capturing';save()
        captured = await post('/api/report-escrows/'+record['escrowId']+'/capture', {'reservation_token':record['reservationToken'],'request_id':record['requestId']+':capture','metadata':{'execution_id':id}}, True)
        # Record what Plaza charged, not the quote: a capture that paid no provider returns the reservation.
        charged = captured_points(captured, points)
        record.update(state='settled', settledPoints=charged);save();store.settle_part(user['id'],local,charged)
        return {**result,'_billing':{'points':charged,'state':'settled','executionId':id}} if isinstance(result,dict) else {'result':result,'_billing':{'points':charged,'state':'settled','executionId':id}}
    except BaseException as error:
        if record['state'] in ('settled','refunded','uncertain'):
            raise  # Settled with Plaza just now (what was delivered was paid, the rest refunded), or left for reconciliation.
        if run is not None and record.get('escrowId') and record['state'] in ('reserved', 'executing'):
            # As the report generator does, a run that does not finish is refunded when it ends (less work Plaza
            # recorded as delivered, which the refund pays and finish() counts as spent). That includes a step cut off
            # by the run's time limit: its provider is paid only through Plaza's delivery record, which Plaza refuses
            # once the escrow is refunded, so the refund cannot take away a payment the provider is owed
            # (Before, a cut-off step was left uncertain and its point stayed reserved.)
            record['failedAt'] = record['state']
            record['state'] = 'refund-pending';save()
            run.hold(finish)
            if isinstance(error, asyncio.CancelledError):
                raise
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
        exact=evidence.get('id')==record['escrowId'] and evidence.get('requestId')==record['requestId'] and scope.get('provider_agent_id')==record['agentId'] and scope.get('pulse_name')==record['pulse'] and scope.get('max_points')==record['points']
        # A refund pays work Plaza recorded as delivered and returns the rest: safe once the scope was delivered, or once
        # its provider answered that the result is lost or held unconfirmed (an escrow-settled provider: Plaza refuses its
        # delivery record after the refund and lets no other execution start the scope).
        # A step of a processor's run is paid only through Plaza's delivery record (escrow-capture), which Plaza refuses
        # once the escrow is refunded: a refund pays the work Plaza recorded as delivered and returns the rest, so it is
        # safe even while the provider has not answered.
        in_run=(record.get('providerBilling') or {}).get('settlement')=='escrow-capture'
        refundable=scope.get('status')=='completed' or (scope.get('status') in ('authorized','running') and (record.get('resultLost') or record.get('resultPending') or in_run))
        if not exact or (scope.get('status')!='paid' and not refundable):raise HTTPException(409,'Plaza has not confirmed this exact provider execution as paid. It remains uncertain and will not be replayed or refunded.')
        record['outcomeEvidence']={k:scope.get(k) for k in ('id','status','result_hash','paid_at')}
        stage='capturing' if scope.get('status')=='paid' else 'refunding'
    if stage not in ('reserving','reserved','capturing','refunding'):
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
    answer=await post('/api/report-escrows/'+record['escrowId']+'/'+action,{'reservation_token':record['reservationToken'],'request_id':record['requestId']+':'+action},True)
    record['state']='settled' if action=='capture' else 'refunded'
    # A refund may still have paid the provider for work Plaza recorded as delivered, and a capture pays only what Plaza
    # settled with a provider; count what each paid.
    settled=captured_points(answer,record['points']) if action=='capture' else delivered_points(answer,record.get('scopeId'),record['points'])
    record['settledPoints']=settled
    store.put(user['id'],'billing',id,{'state':record['state'],'points':record['points'],'agentId':record['agentId'],'private':store.encrypt(record)})
    # The uncertainty reservation can now be resolved with confirmed evidence; a partly paid refund is split.
    if 0<settled<record['points']:
        store.settle_part(user['id'],record['localReservation'],settled)
    else:
        with store.connect() as db:
            db.execute('UPDATE budgets SET status=? WHERE id=? AND owner=?',('settled' if settled else 'released',record['localReservation'],user['id']))
    return {'id':id,'status':record['state'],**({'settledPoints':settled} if action=='refund' else {})}
