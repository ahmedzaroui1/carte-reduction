import html
import re
from urllib.parse import unquote_plus

from odoo import models
from odoo.http import request

from .security_utils import log_security_event


_INJECTION_PATTERNS = (
    ("xss_script", re.compile(r"<\s*script\b", re.IGNORECASE)),
    ("xss_event_handler", re.compile(r"\bonerror\s*=", re.IGNORECASE)),
    ("sqli_boolean", re.compile(r"""['"]\s*or\s+1\s*=\s*1""", re.IGNORECASE)),
    ("sqli_union", re.compile(r"\bunion\s+select\b", re.IGNORECASE)),
    ("sqli_comment", re.compile(r"--")),
)
_IGNORED_KEYS = frozenset(
    {"password", "passwd", "csrf_token", "session_id", "access_token"}
)
_MODEL_METHOD = re.compile(
    r"^/web/dataset/call_kw/(?:res\.partner|cartes\.client)/"
    r"(?:create|write|verify_pos_otp_or_qr)/?$"
)


def _is_monitored_route(path):
    if path == "/web/login":
        return True
    if path in {"/shop", "/my/cartes"}:
        return True
    if path.startswith(("/shop/", "/my/cartes/", "/cartes_reduction/", "/sale/order/")):
        return True
    return bool(_MODEL_METHOD.fullmatch(path))


def _find_signature(value):
    decoded = html.unescape(unquote_plus(html.unescape(value)))
    for signature, pattern in _INJECTION_PATTERNS:
        if pattern.search(decoded):
            return signature
    return False


def _scan(value, key="", budget=None):
    if budget is None:
        budget = [2000]
    budget[0] -= 1
    if budget[0] < 0:
        return "input_complexity_limit"
    if str(key).lower() in _IGNORED_KEYS:
        return False
    if isinstance(value, str):
        return _find_signature(value)
    if isinstance(value, dict) or hasattr(value, "items"):
        for child_key, child_value in value.items():
            signature = _scan(child_value, child_key, budget)
            if signature:
                return signature
    elif isinstance(value, (list, tuple)):
        for child_value in value:
            signature = _scan(child_value, key, budget)
            if signature:
                return signature
    return False


class IrHttpInputGuard(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _dispatch(cls, endpoint):
        http_request = request.httprequest
        path = http_request.path
        if request.db and _is_monitored_route(path):
            parameters = request.params or {}
            if path == "/web/login":
                parameters = {"login": parameters.get("login", "")}
            signature = _scan(parameters)
            if signature:
                log_security_event(
                    request.env,
                    "injection_blocked",
                    details="Blocked input signature=%s route=%s" % (signature, path),
                )
                return request.make_response(
                    "Request rejected by input security validation.",
                    headers=[("Content-Type", "text/plain; charset=utf-8")],
                    status=400,
                )
        return super()._dispatch(endpoint)
