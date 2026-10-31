"""Security/lifecycle envelope around the complete upstream Quart manager."""
from collections import defaultdict, deque
from datetime import timedelta
from functools import wraps
import hmac
import inspect
import secrets
import time

from quart import abort, request, session
from werkzeug.sansio.utils import get_host

from .manager.server import create_app
from .lan import is_local_address, normalize_allowed_hosts


def build_app(plugin):
    secret = plugin.settings['webui']['secret_key']
    if not secret.strip():
        raise ValueError('后台登录密码不能为空或全空白')
    services = {name: value for name, value in vars(plugin.engine).items() if name.endswith(('_service', '_repo'))}
    app = create_app(secret, services)
    app.config.update(MAX_CONTENT_LENGTH=5 * 1024 * 1024, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Strict', PERMANENT_SESSION_LIFETIME=timedelta(hours=2),
                      TRUSTED_HOSTS=normalize_allowed_hosts(plugin.settings['webui'].get(
                          'allowed_hosts', ['localhost', '127.0.0.1', '[::1]'])))
    attempts = defaultdict(deque)

    @app.before_request
    async def secure_all_routes():
        # Enforce explicitly: Quart does not apply Flask's TRUSTED_HOSTS setting.
        get_host(request.scheme, request.headers.get('Host'), trusted_hosts=app.config['TRUSTED_HOSTS'])
        # Hypercorn supplies the ASGI transport peer; peerless scopes are only
        # in-process calls. Never use X-Forwarded-For as proof of LAN origin.
        peer = request.scope.get('client')
        if peer and not is_local_address(peer[0]):
            abort(403)
        session.setdefault('csrf', secrets.token_urlsafe(32))
        if request.path == '/admin/login':
            if request.method == 'POST':
                bucket = attempts[request.remote_addr or 'local']
                now = time.monotonic()
                while bucket and now - bucket[0] > 60:
                    bucket.popleft()
                if len(bucket) >= 5:
                    abort(429)
                bucket.append(now)
            else:
                return
        elif not (session.get('logged_in') and session.get('is_admin')):
            abort(401)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            token = request.headers.get('X-CSRF-Token') or (await request.form).get('csrf_token', '')
            if not hmac.compare_digest(str(token), session['csrf']):
                abort(403)
        if request.path == '/admin/login':
            return

    class RejectedResponse(Exception):
        def __init__(self, response):
            self.response = response

    def transactional_view(view):
        @wraps(view)
        async def dispatch(**kwargs):
            # ASGI body reception belongs to the host loop. The cached request and
            # Quart context are then used only by the worker while we await it.
            await request.get_data()
            await request.form
            await request.files
            async with plugin.lock:
                if not plugin.accepting:
                    abort(503)

                async def execute():
                    before_session = dict(session)
                    try:
                        with plugin.game_transaction() as db:
                            result = await view(**kwargs) if inspect.iscoroutinefunction(view) else view(**kwargs)
                            response = await app.make_response(result)
                            if response.status_code >= 400:
                                db.failed = True
                                raise RejectedResponse(response)
                        return response
                    except BaseException:
                        session.clear()
                        session.update(before_session)
                        raise

                try:
                    return await plugin.worker.run(execute)
                except RejectedResponse as error:
                    return error.response
        return dispatch

    # Keep auth/security on the host loop; execute each whole route transaction
    # in the same serialized boundary as commands/ticks, not per repository call.
    for endpoint, view in list(app.view_functions.items()):
        if endpoint not in ('admin_bp.login', 'admin_bp.logout') and not endpoint.endswith('static'):
            app.view_functions[endpoint] = transactional_view(view)

    @app.after_request
    async def finish(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Cache-Control'] = 'no-store'
        if response.mimetype == 'text/html' and response.status_code < 400:
            body = await response.get_data(as_text=True)
            import re
            token = session['csrf']
            body = re.sub(r'(<form\b[^>]*>)', lambda m: m[1] + f'<input type="hidden" name="csrf_token" value="{token}">', body, flags=re.I)
            # Preserve all original fetch-based admin interfaces, injecting same-origin CSRF.
            script = f'''<script>(function(){{const f=window.fetch;window.fetch=function(u,o={{}}){{if(new URL(typeof u==='string'?u:u.url,location.href).origin===location.origin){{o.headers=new Headers(o.headers);o.headers.set('X-CSRF-Token','{token}');}}return f.call(this,u,o);}};}})();</script>'''
            body = body.replace('<head>', '<head>' + script)
            response.set_data(body)
        return response

    return app
