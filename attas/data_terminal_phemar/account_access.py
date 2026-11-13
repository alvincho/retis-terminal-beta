"""Each Plaza account approves this Terminal for itself.

The account that first approved this installation keeps the installation's own Plaza identity and
registration. Any other account that signs a window in gets its own: its own agent-trust request on
Plaza (identity "<installation>-<12 hex of the account>"), its own approved session and its own
registration, owned by that account. Calls made for a window use the registration of the window's
account, so Plaza sees each account call through a Terminal it owns and approved itself.

An account that has not approved this Terminal yet keeps its own settings and workspaces in it, but every
agent or data call is refused with the reason; its window offers Request access.
"""
import asyncio
import contextvars
import hashlib


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

