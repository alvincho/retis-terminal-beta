"""One payment outcome for a processor's whole run, as the report generator settles a report.

A processor's run is its first call plus every step this Terminal supplies for it (data_requests.run). Each paid
step is reserved and authorized in Plaza as it runs, but nothing is captured until the run returns its result:
then every step is captured. If any step or the run fails, every held step is refunded in full, unless a capture
has already started (an uncertain capture is never followed by a refund). The steps' points together may not exceed
what the Phema may spend for the run.

Nothing here knows a processor's business: a final answer whose `status`, or whose `response.status`, is failed,
error or unavailable is a failed run.
"""
from __future__ import annotations

import contextvars
import logging

from fastapi import HTTPException

LOG = logging.getLogger(__name__)
CURRENT: contextvars.ContextVar = contextvars.ContextVar('terminal_run_escrow', default=None)
FAILED = {'failed', 'error', 'unavailable', 'auth_required', 'credential_required'}


class RunEscrow:
    def __init__(self, ceiling=None):
        self.ceiling = ceiling
        self.points = 0
        self.held = []
        self.capture_started = False

    def admit(self, points):
        """Count a step's quoted points against the run; refuse a step that would pass the Phema's limit."""
        if self.ceiling is not None and self.points + points > self.ceiling:
            raise HTTPException(403, f'This run would cost more than the {self.ceiling} points this Phema may spend.')
        self.points += points

    def hold(self, finish):
        """Keep a reserved step until the run ends; `finish(action)` captures or refunds it."""
        self.held.append(finish)

    async def capture(self):
        self.capture_started = True
        for finish in self.held:
            await finish('capture')

    async def refund(self):
        if self.capture_started:
            return
        for finish in self.held:
            try:
                await finish('refund')
            except Exception as error:
                # The step stays recorded as refund-pending; billing reconciliation can finish it.
                LOG.warning('A held run step could not be refunded yet: %s', str(error)[:200])


def failed(result):
    if not isinstance(result, dict):
        return True
    response = result.get('response') if isinstance(result.get('response'), dict) else {}
    return result.get('status') in FAILED or response.get('status') in FAILED


async def run(user, work):
    """Run `work()` (an async callable) as one escrow outcome for this user's run."""
    escrow = RunEscrow(user.get('_permissionMaxPoints') if isinstance(user.get('_permissionMaxPoints'), int) else None)
    token = CURRENT.set(escrow)
    try:
        result = await work()
    except BaseException:
        await escrow.refund()
        raise
    finally:
        CURRENT.reset(token)
    if failed(result):
        await escrow.refund()
    else:
        await escrow.capture()
    return result
