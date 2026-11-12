"""UNINSTALLED generic adapter for an authenticated mounted-content channel.

The host supplies every authentication, mounting, current permission, registry,
grant and paired network adapter. This module has no routes, agent identities,
business schemas or owner stores. Importing it does not activate a capability.
"""
from copy import deepcopy
import asyncio
import secrets
from contextvars import ContextVar

from prompits.core.capabilities import digest
from phemacast.core.composition import reference_paths, content_hash, validate_composition
from .pulse_forms import PulseFormSession, binding_of, EXTENSION, returned, CAPABILITY, JOB_CAPABILITY
from .content_contract import OPERATIONS, handle_form_operation, transport_marker


def protected_content_request(phema, body):
    """Call before the legacy content handler; never use unknown-op fallback."""
    if not isinstance(body, dict):
        raise ValueError('A structured content request is required.')
    operation = body.get('operation')
    if operation in OPERATIONS:
        return True
    # 'source' sets the data source of the Browser's data panels; it names no reference and refreshes nothing itself.
    if operation not in (None, 'configure', 'capture', 'view', 'interact', 'source'):
        raise ValueError('Unknown content operation cannot fall through to refresh.')
    declared = reference_paths(phema)
    refs = body.get('references', [])
    if not isinstance(refs, list) or any(not isinstance(key, str) for key in refs):
        raise ValueError('References must be declared paths.')
    native = [key for key in refs if EXTENSION in declared.get(key, {}).get('extensions', {})]
    if not native:
        return False
    if len(native) != len(refs) or len(set(refs)) != len(refs):
        raise PermissionError('Protected and legacy references cannot share a dispatch.')
    if operation is not None:
        raise PermissionError('Protected forms require prepare/invoke/observe; refresh only reads.')
    return True


class MountedPulseSourceSdk:
    """Trusted, bounded session wiring for the candidate form broker.

    principal(request) must verify the CURRENT original user and host session.
    read_mount(context,id) must read the CURRENT server-owned mount, never a
    client/Phema declaration or worker-submitted approval. The returned fields
    bind the user, service, window, mount generation, Phema and source mode.
    All adapter credentials stay in their trusted closures, not these contexts.
    """
    def __init__(self, *, principal, verify_request, read_mount, authorize,
                 resolver, issuer, transport, journal, write_ceiling,
                 max_sessions=64, job_ceiling=None):
        adapters = (principal, verify_request, read_mount, authorize, resolver,
                    issuer, transport, write_ceiling)
        if not all(callable(value) for value in adapters):
            raise PermissionError('All current trusted host adapters are required.')
        if job_ceiling is not None and not callable(job_ceiling):
            raise PermissionError('Job admission must be a current trusted host adapter.')
        if type(max_sessions) is not int or not 1 <= max_sessions <= 256:
            raise ValueError('Bounded host session count is required.')
        self.principal, self.verify_request, self.read_mount = principal, verify_request, read_mount
        self.authorize, self.resolver, self.issuer, self.transport = authorize, resolver, issuer, transport
        self.journal, self.write_ceiling = journal, write_ceiling
        self.job_ceiling=job_ceiling
        self.capability_contract=JOB_CAPABILITY if job_ceiling is not None else CAPABILITY
        self.max_sessions = max_sessions
        self.sessions = {}
        self.lock = asyncio.Lock()
        self.request_scope = ContextVar('mounted-pulse-source-request', default=None)

    async def context(self, request, mount):
        context = await returned(self.principal(request))
        if (not isinstance(context, dict) or set(context) != {'actor', 'serviceActor', 'window'}
                or not all(isinstance(value, str) and value for value in context.values())
                or not isinstance(mount, str) or not mount):
            raise PermissionError('Current original user/service/window authentication required.')
        if await returned(self.verify_request(request, context)) is not True:
            raise PermissionError('Current host session and request protection required.')
        return deepcopy(context)

    async def mounted(self, context, mount):
        value = await returned(self.read_mount(deepcopy(context), mount))
        required = {'actor', 'serviceActor', 'window', 'mount', 'generation', 'active', 'phema', 'sourceMode'}
        if (not isinstance(value, dict) or set(value) != required
                or any(value.get(key) != context[key] for key in context)
                or value.get('mount') != mount or value.get('active') is not True
                or type(value.get('generation')) is not int or value['generation'] < 0
                or value.get('sourceMode') != 'parameters'):
            raise PermissionError('Current owned parameter mount is unavailable.')
        # Validate the full nested reference tree before accepting any path.
        validate_composition(value['phema'])
        return deepcopy(value)

    async def _session(self, request, mount):
        context = await self.context(request, mount)
        mounted = await self.mounted(context, mount)
        phema_digest = content_hash(mounted['phema'])
        generation = mounted['generation']
        generation_mount = digest([mount, generation])
        key = (context['actor'], context['serviceActor'], context['window'], mount)
        stamp = (generation, phema_digest)
        async with self.lock:
            record = self.sessions.get(key)
            if record and record['stamp'] == stamp:
                # Keep sessions in order of use, longest unused first.
                self.sessions[key] = self.sessions.pop(key)
                return record['session'], mounted
            if record:
                record['session'].close()
                del self.sessions[key]
            # A closing mount releases its session through close(). One opened
            # while its mount was closing, or left by a host that never calls
            # close(), must not lock every window out: make room by closing the
            # longest unused session instead of refusing.
            while len(self.sessions) >= self.max_sessions:
                self.sessions.pop(next(iter(self.sessions)))['session'].close()

            async def current():
                fresh = await self.context(self.request_scope.get(), mount)
                value = await self.mounted(fresh, mount)
                return {**fresh, 'mount': digest([mount, value['generation']]),
                        'phemaDigest': content_hash(value['phema']), 'sourceMode': value['sourceMode']}

            async def reference_reader(path):
                value = await self.mounted(await self.context(self.request_scope.get(), mount), mount)
                ref = reference_paths(value['phema']).get(path)
                if ref is None:
                    raise PermissionError('Reference is outside the selected Phema tree.')
                return ref

            async def admitted(actor, service, window, scoped_mount, release, ref, resource):
                fresh = await self.context(self.request_scope.get(), mount)
                value = await self.mounted(fresh, mount)
                if ({'actor': actor, 'serviceActor': service, 'window': window} != fresh
                        or scoped_mount != digest([mount, value['generation']])
                        or release != content_hash(value['phema'])
                        or not any(ref == item for item in reference_paths(value['phema']).values())):
                    return False
                if resource['effects']['writesState']:
                    if await returned(self.write_ceiling(fresh, mount, release, ref, resource)) is not True:
                        return False
                if resource['effects']['startsJob']:
                    if (self.job_ceiling is None or await returned(
                            self.job_ceiling(fresh,mount,release,ref,resource)) is not True):
                        return False
                return await returned(self.authorize(fresh, mount, release, ref, resource)) is True

            async def resolve(ref):
                fresh = await self.context(self.request_scope.get(), mount)
                return await returned(self.resolver(fresh, ref))

            async def issue(**kwargs):
                fresh = await self.context(self.request_scope.get(), mount)
                if kwargs.get('actor') != fresh['actor']:
                    raise PermissionError('Original grant user changed.')
                return await returned(self.issuer(fresh, **kwargs))

            async def transport(resource, wire, grant):
                fresh = await self.context(self.request_scope.get(), mount)
                return await returned(self.transport(fresh, resource, wire, grant))

            session = PulseFormSession(actor=context['actor'], service_actor=context['serviceActor'],
                window=context['window'], mount=generation_mount, phema_digest=phema_digest,
                reference_reader=reference_reader, resolver=resolve, current_context=current,
                authorize_reference=admitted, issuer=issue, transport=transport,
                journal=self.journal, allow_writes=True,allow_jobs=self.job_ceiling is not None)
            self.sessions[key] = {'stamp': stamp, 'session': session}
            return session, mounted

    async def capability(self, request, mount):
        # Emit into host-owned content state only after successful adoption.
        token = self.request_scope.set(request)
        try:
            await self._session(request, mount)
            return deepcopy(self.capability_contract)
        finally:
            self.request_scope.reset(token)

    async def describe(self, request, mount):
        """Current host adoption plus durable, observe-only recovery pointers."""
        token=self.request_scope.set(request)
        try:
            session,mounted=await self._session(request,mount)
            return {'transportCapability':deepcopy(self.capability_contract),
                    'pendingInteractions':await session.recoverable(reference_paths(mounted['phema']))}
        finally:self.request_scope.reset(token)

    async def handle(self, request, mount, body):
        if not isinstance(body, dict):
            raise ValueError('A structured content request is required.')
        token = self.request_scope.set(request)
        try:
            return await self._handle(request, mount, body)
        finally:
            self.request_scope.reset(token)

    async def _handle(self, request, mount, body):
        session, mounted = await self._session(request, mount)
        if body.get('operation') in OPERATIONS:
            return await handle_form_operation(session, body)
        if not protected_content_request(mounted['phema'], body):
            raise ValueError('This adapter only handles protected content requests.')
        if set(body) != {'references'} or not body['references']:
            raise ValueError('Refresh accepts only selected protected read references.')
        # Manual refresh uses host-generated known intent/wire journal entries.
        # It never invokes an interactive reference or an operation with effects.
        for path in body['references']:
            ref = await session.reference_reader(path)
            resource = await session.resource(binding_of(ref)['operationRef'])
            if ref.get('interaction') or any(resource['effects'].values()):
                raise PermissionError('Refresh cannot execute an interactive operation or effect.')
        results = {}
        for path in body['references']:
            key = 'read-' + secrets.token_hex(24)
            prepared = await session.prepare({'reference': path, 'inputs': {}, 'requestKey': key})
            if prepared['state'] != 'prepared':
                raise PermissionError('Read intent is already uncertain; never replay it.')
            result = await session.invoke({'reference': path, 'requestKey': key,
                                           'wireDigest': prepared['wireDigest']})
            results[path] = result
        return {'transportCapability': deepcopy(self.capability_contract), 'results': results,
                'executionReplayAllowed': False}

    def close(self, *, actor, window, mount):
        """Release the sessions of a mount the host has closed."""
        for key in list(self.sessions):
            if (key[0], key[2], key[3]) == (actor, window, mount):
                self.sessions.pop(key)['session'].close()

    def close_all(self):
        for record in self.sessions.values():
            record['session'].close()
        self.sessions.clear()
