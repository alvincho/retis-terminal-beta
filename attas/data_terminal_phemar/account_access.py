"""Each Plaza account approves this Terminal for itself.

The account that first approved this installation keeps the installation's own Plaza identity and
registration. Any other account that signs a window in gets its own: its own agent-trust request on
Plaza (identity "<installation>-<12 hex of the account>"), its own approved session and its own
registration, owned by that account. Calls made for a window use the registration of the window's
account, so Plaza sees each account call through a Terminal it owns and approved itself.

The Terminal checks the account's approval at every sign-in. An account that has not approved this Terminal
asks for it at once, as itself (ensure): where Plaza approves automatically, the window is ready right away.
Until then the Terminal opens no Phema and calls no agent for it; a Terminal or account the Plaza owner
blocked gets no further access.
"""
import asyncio
import contextvars
import hashlib
import sys
import time

from fastapi import HTTPException


# The registration calls for the current request use: None is the installation's own.
CURRENT = contextvars.ContextVar('terminal_account_registration', default=None)

NOT_APPROVED = 'This account has not approved this Terminal yet. Open Terminal settings → Plaza account and choose Request access.'
WRONG_ACCOUNT = 'Another account requested access for this window. Sign in to Plaza as this window’s account, then request access again.'


def account_suffix(account):
    return hashlib.sha256(str(account).encode()).hexdigest()[:12]


def account_scope(scope, account):
    """The account's own enrollment scope; its last part is the account's agent identity."""
    return scope + '-' + account_suffix(account)


class NotApproved:
    """Stands in for the registration of an account that has not approved this Terminal."""
    agent_id = None
    agent_name = ''
    public_url = ''
    _token = None
    registered = False
    state = 'not-approved'


class AccountAccess:
    """The per-account enrollments and registrations of one Terminal installation."""

    INDEX = 'account-access'

    def __init__(self, store, scope, make_enrollment, make_registration):
        self.store, self.scope = store, scope
        self.make_enrollment, self.make_registration = make_enrollment, make_registration
        self.enrollments, self.registrations, self.tasks = {}, {}, {}
        self.locks, self.retry_at, self.refused, self.pending = {}, {}, {}, {}

    def owner(self):
        return self.store.installation_owner(self.scope)

    def other(self, account):
        """An account that is not the installation's first approver, once the installation has one."""
        owner = self.owner()
        return bool(owner and account and str(account) != owner)

    def approved(self, account):
        if not account:
            return False
        owner = self.owner()
        if not owner or str(account) == owner:
            return bool(owner)
        return self.store.installation_owner(account_scope(self.scope, account)) == str(account)

    def accounts(self):
        return [row['account'] for row in self.store.list('_installation', self.INDEX) if row.get('account')]

    def enrollment(self, account):
        account = str(account)
        if account not in self.enrollments:
            self.store.put('_installation', self.INDEX, account_suffix(account), {'account': account})
            registration = self.registration(account)
            self.enrollments[account] = self.make_enrollment(account_scope(self.scope, account), registration, account)
        return self.enrollments[account]

    def registration(self, account):
        account = str(account)
        if account not in self.registrations:
            self.registrations[account] = self.make_registration(account, account_scope(self.scope, account))
        return self.registrations[account]

    def current(self, account):
        """The registration calls made for `account` use: None for the installation's own."""
        if not self.other(account):
            return None
        return self.registration(account) if self.approved(account) else NotApproved()

    def bind(self, account):
        CURRENT.set(self.current(account))

    def blocked(self, account):
        """Why Plaza refused this account's approval for good (a block), or None."""
        if not self.other(account):
            return None
        reason = self.refused.get(str(account))
        if reason:
            return reason
        state = ((self.enrollments.get(str(account)) or self.enrollment(account)).saved() or {}).get('state') or {}
        return state.get('blocked')

    def ensure_soon(self, account, access_token, here=None):
        """Start ensure in the background, once at a time per account (a request that finds no approval)."""
        account = str(account)
        task = self.pending.get(account)
        if (task and not task.done()) or self.retry_at.get(account, 0) > time.monotonic():
            return
        self.pending[account] = asyncio.create_task(self.ensure(account, access_token, here))

    async def ensure(self, account, access_token, here=None):
        """At sign-in: an account that has not approved this Terminal asks for it now, with its own Terminal session.
        Returns 'approved', 'pending' (the Plaza owner reviews it), 'blocked' or 'unavailable' (try again later)."""
        account = str(account)
        if not self.other(account) or self.approved(account):
            return 'approved'
        lock = self.locks.setdefault(account, asyncio.Lock())
        async with lock:
            if self.approved(account):
                return 'approved'
            if self.retry_at.get(account, 0) > time.monotonic():
                return 'blocked' if self.blocked(account) else 'unavailable'
            enrollment = self.enrollment(account)
            try:
                state = await enrollment.begin(here)
                if state.get('status') == 'draft':
                    await enrollment.submit_with_session(access_token)
                result = await enrollment.status(connect=True)
            except HTTPException as exc:
                self.retry_at[account] = time.monotonic() + 30
                print(time.strftime('%Y-%m-%d %H:%M:%S%z') + ' WARNING:  Plaza did not take an account\'s request to use this Terminal: '
                      + str(exc.status_code) + ' ' + str(exc.detail)[:200] + '; trying again in about 30 seconds.', file=sys.stderr, flush=True)
                if exc.status_code == 403 and isinstance(exc.detail, str) and 'blocked' in exc.detail:
                    self.refused[account] = exc.detail
                    return 'blocked'
                return 'unavailable'
            self.refused.pop(account, None)
            self.watch(account)
            if result.get('blocked'):
                return 'blocked'
            if result.get('status') == 'signed-in' or self.approved(account):
                await self.registration(account).start()
                return 'approved'
            return 'pending'

    def find_return(self, state):
        """The account enrollment a returning Plaza tab belongs to."""
        for account in self.accounts():
            saved = self.enrollment(account).saved() or {}
            expected = saved.get('returnState')
            if expected and expected == hashlib.sha256(state.encode()).hexdigest():
                return self.enrollments[account]
        return None

    async def start(self):
        for account in self.accounts():
            enrollment = self.enrollment(account)
            if self.approved(account):
                await self.registration(account).start()
            self.tasks[account] = asyncio.create_task(enrollment.watch())

    def watch(self, account):
        if account not in self.tasks or self.tasks[account].done():
            self.tasks[account] = asyncio.create_task(self.enrollment(account).watch())

    async def stop(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
        await asyncio.gather(*(registration.stop() for registration in self.registrations.values()), return_exceptions=True)

    async def forget(self):
        results = await asyncio.gather(*(registration.forget() for registration in self.registrations.values()), return_exceptions=True)
        return all(result is True for result in results)

