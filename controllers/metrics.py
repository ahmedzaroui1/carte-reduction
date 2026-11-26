import hmac
import time

from odoo import http
from odoo.http import request


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
    "login_failed",
)

_AUDIT_EVENT_TYPES = (
    "cin_reveal",
    "pin_set",
    "pin_failed",
    "pos_otp_failed",
    "redemption",
    "velocity_abuse",
    "ml_integrity_failure",
    "unauthorized_access",
    "tampered_token",
    "replay_blocked",
    "otp_expired",
    "double_spend_blocked",
    "idor_blocked",
    "injection_blocked",
    "hmac_invalid",
    "seal_verify",
    "login",
    "logout",
    "login_failed",
    "pos_session_opened",
    "pos_session_closed",
)

_METRIC_ROLES = ("cashier", "ecommerce", "admin", "other", "unknown")


def _read_metrics_token():
    try:
        with open("/run/secrets/metrics_bearer_token", "rb") as token_file:
            token = token_file.read().strip()
    except OSError as error:
        raise RuntimeError("Metrics authentication is unavailable.") from error
    if len(token) < 32:
        raise RuntimeError("The metrics bearer token is invalid.")
    return token


def _role_for_user(user):
    if not user or not user.exists():
        return "unknown"
    if user.has_group("base.group_system") or user.has_group(
        "cartes_reduction.cartes_manager_group"
    ):
        return "admin"
    if user.has_group("point_of_sale.group_pos_user"):
        return "cashier"
    if user.has_group("base.group_portal"):
        return "ecommerce"
    return "other"


def _escape_label(value):
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


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
            expected_token = _read_metrics_token()
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
            "SELECT event_type, user_id, logged_role, COUNT(*) "
            "FROM ("
            "SELECT event_type, user_id, "
            "CASE "
            "WHEN event_type IN ('login', 'logout', 'login_failed') "
            "AND details LIKE '%Rôle RBAC : Caissier POS restreint%' THEN 'cashier' "
            "WHEN event_type IN ('login', 'logout', 'login_failed') "
            "AND details LIKE '%Rôle RBAC : Client Portail%' THEN 'ecommerce' "
            "WHEN event_type IN ('login', 'logout', 'login_failed') "
            "AND (details LIKE '%Rôle RBAC : Administrateur%' "
            "OR details LIKE '%Rôle RBAC : Gestionnaire Cartes%') THEN 'admin' "
            "WHEN event_type IN ('login', 'logout', 'login_failed') "
            "AND details LIKE '%Rôle RBAC : Compte inconnu%' THEN 'unknown' "
            "WHEN event_type IN ('login', 'logout', 'login_failed') "
            "AND details LIKE '%Rôle RBAC : Utilisateur interne%' THEN 'other' "
            "ELSE NULL END AS logged_role "
            "FROM cartes_reduction_security_audit"
            ") AS audit_events "
            "GROUP BY event_type, user_id, logged_role"
        )
        audit_rows = request.env.cr.fetchall()
        user_ids = {
            user_id for _, user_id, logged_role, _ in audit_rows if user_id and not logged_role
        }
        users = request.env["res.users"].sudo().browse(list(user_ids)).exists()
        role_by_user = {user.id: _role_for_user(user) for user in users}
        event_counts = {event_type: 0 for event_type in _AUDIT_EVENT_TYPES}
        role_event_counts = {
            (role, event_type): 0
            for role in _METRIC_ROLES
            for event_type in _AUDIT_EVENT_TYPES
        }
        for event_type, user_id, logged_role, count in audit_rows:
            event_type = str(event_type or "unknown")
            count = int(count)
            event_counts[event_type] = event_counts.get(event_type, 0) + count
            role = logged_role or role_by_user.get(user_id, "unknown")
            role_event_counts[(role, event_type)] = (
                role_event_counts.get((role, event_type), 0) + count
            )
        incident_count = sum(event_counts.get(event_type, 0) for event_type in _INCIDENT_TYPES)
        seal = integrity.get("seal", {})
        valid = bool(integrity.get("valid"))
        lines = [
            "# HELP odoo_audit_seal_integrity External audit seal integrity status.",
            "# TYPE odoo_audit_seal_integrity gauge",
            'odoo_audit_seal_integrity{status="valid"} %d' % int(valid),
            'odoo_audit_seal_integrity{status="invalid"} %d' % int(not valid),
            "# HELP odoo_audit_seal_head_match Whether the external seal head matches PostgreSQL.",
            "# TYPE odoo_audit_seal_head_match gauge",
            "odoo_audit_seal_head_match %d" % int(bool(seal.get("head_match"))),
            "# HELP odoo_audit_seal_sequence Latest validated external seal sequence.",
            "# TYPE odoo_audit_seal_sequence gauge",
            "odoo_audit_seal_sequence %d" % int(seal.get("sequence") or 0),
            "# HELP odoo_audit_seal_last_verification_timestamp Unix time of verification.",
            "# TYPE odoo_audit_seal_last_verification_timestamp gauge",
            "odoo_audit_seal_last_verification_timestamp %d" % int(time.time()),
            "# HELP odoo_audit_seal_failures_total Seal verification or append failures.",
            "# TYPE odoo_audit_seal_failures_total counter",
            "odoo_audit_seal_failures_total %d" % _seal_failures(),
            "# HELP odoo_security_audit_entries_total Number of chained audit entries.",
            "# TYPE odoo_security_audit_entries_total gauge",
            "odoo_security_audit_entries_total %d" % sum(event_counts.values()),
            "# HELP odoo_security_incidents_total Number of security incidents.",
            "# TYPE odoo_security_incidents_total gauge",
            "odoo_security_incidents_total %d" % incident_count,
            "# HELP odoo_security_audit_events_total Audit entries by event type.",
            "# TYPE odoo_security_audit_events_total counter",
        ]
        for event_type, count in sorted(event_counts.items()):
            label = _escape_label(event_type)
            lines.append(
                'odoo_security_audit_events_total{event_type="%s"} %d' % (label, int(count))
            )
        lines.extend(
            [
                "# HELP odoo_security_audit_events_by_role_total Audit entries by actor role and event type.",
                "# TYPE odoo_security_audit_events_by_role_total counter",
            ]
        )
        for (role, event_type), count in sorted(role_event_counts.items()):
            lines.append(
                'odoo_security_audit_events_by_role_total{role="%s",event_type="%s"} %d'
                % (_escape_label(role), _escape_label(event_type), count)
            )
        return request.make_response(
            "\n".join(lines) + "\n",
            headers=[("Content-Type", "text/plain; version=0.0.4; charset=utf-8")],
        )


def _seal_failures():
    from odoo.addons.cartes_reduction.models.audit_seal_manager import (
        get_seal_failures_total,
    )

    return get_seal_failures_total()
