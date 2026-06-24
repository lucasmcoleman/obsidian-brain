"""Bearer-token authentication for the streamable-HTTP MCP transport.

The HTTP server exposes mutating vault tools; without auth, anyone who can reach
the port can read and write the vault (audit finding H1). This middleware rejects
any request lacking the configured shared secret, except a small allowlist of
public paths (e.g. the ``/health`` liveness probe). Auth is opt-in: the server
only installs this middleware when ``BRAIN_AUTH_TOKEN`` is set, preserving the
existing no-token behavior for purely network-isolated deployments.
"""
import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str, public_paths=()):
        super().__init__(app)
        self._expected = f"Bearer {token}"
        self.public_paths = set(public_paths)

    async def dispatch(self, request, call_next):
        if request.url.path in self.public_paths:
            return await call_next(request)
        presented = request.headers.get("authorization", "")
        # Constant-time compare to avoid leaking the secret via timing.
        if not hmac.compare_digest(presented, self._expected):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)
