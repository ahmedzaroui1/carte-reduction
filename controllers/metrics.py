import hmac
import time

from odoo import http
from odoo.http import request

from odoo.addons.cartes_reduction.models.security_audit import (
    get_seal_failures_total,
)
from odoo.addons.cartes_reduction.models.security_utils import get_metrics_bearer_token


_INCIDENT_TYPES = (
    "cin_reveal",
    "pin_failed",
    "pos_otp_failed",
    "otp_expired",
    "velocity_abuse",
    "ml_integrity_failure",
    "unauthorized_access",
    "idor_blocked",
    "injection_blocked",
    "tampered_token",
    "hmac_invalid",
    "replay_blocked",
    "double_spend_blocked",
)


class CartesReductionMetricsController(http.Controller):

    @http.route(
        "/cartes_reduction/metrics",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
        save_session=False,
    )
    def metrics(self, **kwargs):
        try:
            expected_token = get_metrics_bearer_token()
        except RuntimeError:
            return request.make_response(
                "Metrics authentication is unavailable.",
                headers=[("Content-Type", "text/plain; charset=utf-8")],
                status=503,
            )

        supplied = request.httprequest.headers.get("Authorization", "").encode("utf-8")
        if not hmac.compare_digest(supplied, b"Bearer " + expected_token):
            return request.make_response(
                "Forbidden",
                headers=[("Content-Type", "text/plain; charset=utf-8")],
                status=403,
            )

        audit_model = request.env["cartes.reduction.security.audit"].sudo()
        integrity = audit_model.verify_integrity()
        request.env.cr.execute(
            """
            SELECT event_type, COUNT(*)
              FROM cartes_reduction_security_audit
             GROUP BY event_type
            """
        )
        event_counts = dict(request.env.cr.fetchall())
        incident_count = sum(event_counts.get(event_type, 0) for event_type in _INCIDENT_TYPES)
        seal = integrity.get("seal", {})
        seal_valid = bool(integrity.get("valid"))
        lines = [
            "# HELP odoo_audit_seal_integrity External audit seal integrity status.",
            "# TYPE odoo_audit_seal_integrity gauge",
            f'odoo_audit_seal_integrity{{status="valid"}} {int(seal_valid)}',
            f'odoo_audit_seal_integrity{{status="invalid"}} {int(not seal_valid)}',
            "# HELP odoo_audit_seal_head_match Whether the external seal head matches PostgreSQL.",
            "# TYPE odoo_audit_seal_head_match gauge",
            f"odoo_audit_seal_head_match {int(bool(seal.get('head_match')))}",
            "# HELP odoo_audit_seal_sequence Latest validated external seal sequence.",
            "# TYPE odoo_audit_seal_sequence gauge",
            f"odoo_audit_seal_sequence {int(seal.get('sequence') or 0)}",
            "# HELP odoo_audit_seal_last_verification_timestamp Unix time of this verification.",
            "# TYPE odoo_audit_seal_last_verification_timestamp gauge",
            f"odoo_audit_seal_last_verification_timestamp {int(time.time())}",
            "# HELP odoo_audit_seal_failures_total Seal verification or append failures.",
            "# TYPE odoo_audit_seal_failures_total counter",
            f"odoo_audit_seal_failures_total {get_seal_failures_total()}",
            "# HELP odoo_security_audit_entries_total Number of chained audit entries.",
            "# TYPE odoo_security_audit_entries_total gauge",
            f"odoo_security_audit_entries_total {sum(event_counts.values())}",
            "# HELP odoo_security_incidents_total Number of security incident audit entries.",
            "# TYPE odoo_security_incidents_total gauge",
            f"odoo_security_incidents_total {incident_count}",
            "# HELP odoo_security_audit_events_total Audit entries by event type.",
            "# TYPE odoo_security_audit_events_total counter",
        ]
        for event_type, count in sorted(event_counts.items()):
            escaped_type = str(event_type or "unknown").replace("\\", "\\\\").replace('"', '\\"')
            lines.append(
                f'odoo_security_audit_events_total{{event_type="{escaped_type}"}} {int(count)}'
            )
        return request.make_response(
            "\n".join(lines) + "\n",
            headers=[("Content-Type", "text/plain; version=0.0.4; charset=utf-8")],
        )
