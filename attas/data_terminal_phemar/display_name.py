"""The name this Terminal reports to Plaza while it runs: "<session name> - Data Terminal".

The session name is the one its owner gave the most recently used named window of
this installation. Plaza passes it only to the Terminal's owner and administrators;
the registered name and identity stay as they are.
"""
import time

from .window_sessions import saved_tabs

LABEL = 'Data Terminal'


def label(store, owner):
    """The most recently used named window's session name with the Terminal's name, or the name alone."""
    if not owner:
        return LABEL
    for tab in saved_tabs(store, {'id': owner}):
        name = ' '.join(str(tab.get('name') or '').split())
        if name:
            return name + ' - ' + LABEL
    return LABEL


class DisplayName:
    """Read at most once a minute; the heartbeat asks every few seconds."""

    def __init__(self, read, *, ttl=60, clock=time.monotonic):
        self.read, self.ttl, self.clock = read, ttl, clock
        self.value, self.at = LABEL, None

    def __call__(self):
        now = self.clock()
        if self.at is None or now - self.at >= self.ttl:
            try:
                self.value = self.read() or LABEL
            except Exception:
                pass  # Keep the last name; a missing name never stops the heartbeat.
            self.at = now
        return self.value
