"""Proof that a request comes from this computer's Terminal user, not merely from something that reaches its port.

A loopback port is reachable by other accounts on the computer, containers, the Windows side of WSL and
sandboxed apps, and those can send any Host header and no Origin. The launcher therefore writes a secret
for each launch to a file only its user can read and prints the Terminal's address with that secret in the
URL fragment, which no server log records. The page exchanges it once for an HttpOnly cookie. A browser
that already holds a login for this Terminal is proof as well, so signed-in browsers carry on after a
restart without opening the printed address again.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import time

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from .window_sessions import WINDOW, browser_digest, browser_proof, window_scope

ENV = 'ATTAS_LOCAL_ACCESS_FILE'
MESSAGE = ('Open this Terminal from the address that "python3 terminal.py up" printed, '
           'or run "python3 terminal.py open".')


def launcher_app(environ=os.environ):
    """The app that started this Terminal and opens it from its icon's menu, e.g. 'Retis Terminal'; '' for the kit's
    terminal.py or a direct start. Its menu, not a printed address, is how its users open the Terminal."""
    prog = str(environ.get('TERMINAL_LAUNCHER_PROG') or '').strip()
    return '' if not prog or 'terminal.py' in prog or len(prog) > 60 else prog


def message(environ=os.environ):
    """What a browser without local access is told."""
    app = launcher_app(environ)
    return f'Open this Terminal from the {app} icon\'s menu: choose Open.' if app else MESSAGE
# Sign-in, Plaza connection and installation routes.
GATED = ('/api/auth/', '/api/trusted-plazas', '/api/installation-language', '/api/plaza-connection')
# A handoff ticket is its own proof; Plaza's approvals return from another site, which carries no SameSite cookie.
OPEN = ('/api/auth/browser-handoff/', '/api/auth/window-signin/return', '/api/auth/connect/return')
# The launcher's own proof, for what only it may do (removing the Terminal from Plaza). The access
# cookie cannot serve: a signed-in browser is given it again after a restart without the launch secret.
LAUNCHER_HEADER = 'X-Terminal-Launcher-Proof'


def launcher_proof(secret):
    """What the launcher, which reads this launch's secret from its file, sends in LAUNCHER_HEADER. Derived apart from
    the access cookie and never set as a cookie, so no browser access renewal issues it."""
    return hmac.new(secret.encode(), b'attas-terminal-launcher|forget-registration', hashlib.sha256).hexdigest()


def own_origin(request):
    """Where Plaza returns approvals for this Terminal: its public origin when hosted, else the address this browser uses."""
    from urllib.parse import urlsplit
    public = os.environ.get('ATTAS_PUBLIC_ORIGIN', '').rstrip('/')
    return public if public and urlsplit(public).hostname not in ('127.0.0.1', 'localhost', '::1') else str(request.base_url).rstrip('/')


def write_secret(path):
    """A new secret for this launch, readable only by this user; returns it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    token = secrets.token_urlsafe(32)
    temporary = path.with_suffix('.tmp')
    temporary.unlink(missing_ok=True)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump({'token': token}, handle)
    temporary.replace(path)
    return token


def read_secret(path):
    try:
        token = json.loads(Path(path).read_text()).get('token')
    except (OSError, ValueError):
        return None
    return token if isinstance(token, str) and len(token) >= 32 else None


class LocalAccess:
    def __init__(self, store, scope, cookie, generic_cookie, port, path=None):
        path = path if path is not None else os.environ.get(ENV)
        self.secret = read_secret(path) if path else None
        self.store, self.scope = store, scope
        self.session_cookies = {cookie, generic_cookie}
        self.window_prefix, self.remember = cookie + '_w_', cookie + '_remember'
        # Cookies ignore ports, so two Terminals on one computer need different names.
        self.name = f'attas_local_access_{port}'

    def value(self, browser):
        """This launch's access cookie for the browser holding this key (its digest). Browsers send a loopback host's
        cookies to every port on it; bound to the key the Terminal origin's own storage keeps
        (window_sessions.BROWSER_HEADER), a copy that reaches another local service proves nothing."""
        if not self.secret or not browser:
            return ''
        return hmac.new(self.secret.encode(), b'attas-terminal-local-access|' + browser.encode(), hashlib.sha256).hexdigest()

    @property
    def enabled(self):
        return bool(self.secret)

    def granted(self, request):
        if not self.enabled:
            return True
        expected = self.value(browser_digest(request))
        return bool(expected) and hmac.compare_digest(request.cookies.get(self.name, ''), expected)

    def launcher(self, request):
        """Whether the request comes from this launch's launcher: it proves the launch secret itself (LAUNCHER_HEADER)."""
        if not self.enabled:
            return False
        return hmac.compare_digest(request.headers.get(LAUNCHER_HEADER, ''), launcher_proof(self.secret))

    def proven(self, request):
        """Whether the request carries a live login of this Terminal (window, installation or remembered), from the
        browser it was issued to."""
        now, browser = time.time(), browser_digest(request)
        if not browser:
            return False
        with self.store.connect() as db:
            for name, value in request.cookies.items():
                if not value or len(value) > 200:
                    continue
                if name == self.remember:
                    if db.execute('SELECT 1 FROM remembered_logins WHERE proof=? AND terminal=? AND browser=? AND expires>?',
                                  (hashlib.sha256(value.encode()).hexdigest(), self.scope, browser, now)).fetchone():
                        return True
                    continue
                if name in self.session_cookies:
                    terminal = self.scope
                elif name.startswith(self.window_prefix) and WINDOW.fullmatch(name[len(self.window_prefix):]):
                    terminal = window_scope(self.scope, name[len(self.window_prefix):])
                else:
                    continue
                if self.store.browser_login(terminal, value, browser):
                    return True
        return False

    def allowed(self, request):
        return self.granted(request) or self.proven(request)

    def status(self, request):
        return 'off' if not self.enabled else 'granted' if self.allowed(request) else 'required'

    def gated(self, path):
        return path.startswith(GATED) and not path.startswith(OPEN)

    def require(self, request):
        if not self.allowed(request):
            raise HTTPException(403, MESSAGE)

    def grant(self, request, response):
        response.set_cookie(self.name, self.value(browser_proof(request)), httponly=True, samesite='strict', path='/',
                            secure=request.url.scheme == 'https')

    def refresh(self, request, response):
        """Give the access cookie to a browser that proves itself with a login, e.g. after a restart."""
        if self.enabled and not self.granted(request) and self.proven(request):
            self.grant(request, response)

    def install(self, app):
        @app.post('/api/local-access')
        async def claim(request: Request):
            body = await request.json()
            token = body.get('token') if isinstance(body, dict) else None
            if not self.enabled or not isinstance(token, str) or not hmac.compare_digest(token, self.secret):
                raise HTTPException(403, MESSAGE)
            browser_proof(request)  # the cookie is issued to this browser's key
            response = JSONResponse({'localAccess': 'granted'}, headers={'Cache-Control': 'no-store'})
            self.grant(request, response)
            return response
