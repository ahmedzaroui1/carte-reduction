from odoo import http
from odoo.addons.auth_signup.controllers.main import AuthSignupHome
from odoo.addons.website.controllers.main import Website as WebsiteController
from odoo.addons.web.controllers.session import Session
from odoo.http import request

from odoo.addons.cartes_reduction.models.security_utils import (
    log_security_event,
    reject_suspicious_input,
)


def _actor_scope(user):
    if not user:
        return "unknown"
    if user.has_group("base.group_system") or user.has_group(
        "cartes_reduction.cartes_manager_group"
    ):
        return "administrator"
    if user.has_group("point_of_sale.group_pos_user"):
        return "pos_cashier"
    if user.has_group("base.group_portal"):
        return "ecommerce_client"
    return "unknown"


def _log_auth_event(event_type, user=False, scope="unknown", source="internal"):
    log_security_event(
        request.env,
        event_type,
        user_id=user.id if user else False,
        actor_scope=_actor_scope(user) if user else scope,
        details=f"source={source}",
    )


class SotufabAuthSignupHome(AuthSignupHome):

    def _request_portal_mode(self):
        return request.httprequest.headers.get("X-Sotufab-Portal")

    def _reject_portal_login(self):
        request.session.logout(keep_db=True)
        return request.make_response(
            "Accès refusé. Utilisez le portail correspondant à votre compte.",
            headers=[("Content-Type", "text/plain; charset=utf-8")],
            status=403,
        )

    def _route_authenticated_user(self):
        uid = request.session.uid
        mode = self._request_portal_mode()
        if not uid or mode not in ("internal", "public"):
            return None

        user = request.env["res.users"].sudo().browse(uid)
        if not user.exists():
            return self._reject_portal_login()

        is_portal = user.has_group("base.group_portal")
        is_internal = user.has_group("base.group_user")
        is_manager = user.has_group(
            "base.group_system"
        ) or user.has_group("cartes_reduction.cartes_manager_group")
        is_cashier = user.has_group("point_of_sale.group_pos_user")

        if mode == "internal":
            if is_portal:
                return self._reject_portal_login()
            if is_manager:
                return request.redirect("/web", 303)
            if is_internal and is_cashier:
                return request.redirect("/pos/ui", 303)
            return self._reject_portal_login()

        if is_internal or is_manager or is_cashier:
            return self._reject_portal_login()
        if is_portal:
            return request.redirect("/shop", 303)
        return self._reject_portal_login()

    def _render_internal_login(self, response):
        context = getattr(response, "qcontext", {}) or {}
        return request.render(
            "cartes_reduction.internal_staff_login",
            {
                "db": request.db,
                "disable_database_manager": True,
                "disable_footer": True,
                "error": context.get("error"),
                "form_small": False,
                "login": context.get("login", ""),
                "login_card_classes": "sotufab-internal-login",
                "message": context.get("message"),
                "redirect": "/web",
            },
        )

    def _audit_auth_event(self, event_type, user=False, scope=False):
        _log_auth_event(
            event_type,
            user=user,
            scope=scope or "unknown",
            source="public" if self._request_portal_mode() == "public" else "internal",
        )

    @http.route()
    def web_login(self, *args, **kwargs):
        if (
            request.db
            and request.httprequest.method == "POST"
            and reject_suspicious_input(
                request.env,
                "/web/login",
                {"login": request.params.get("login")},
            )
        ):
            return request.make_response(
                "Requête suspecte rejetée.",
                headers=[("Content-Type", "text/plain; charset=utf-8")],
                status=400,
            )
        response = super().web_login(*args, **kwargs)
        if request.db and request.httprequest.method == "POST":
            if request.session.uid:
                user = request.env["res.users"].sudo().browse(request.session.uid).exists()
                self._audit_auth_event("login", user=user)
            else:
                login = request.params.get("login")
                user = (
                    request.env["res.users"].sudo().search([("login", "=", login)], limit=1)
                    if login
                    else False
                )
                scope = (
                    "ecommerce_client"
                    if self._request_portal_mode() == "public"
                    else "unknown"
                )
                self._audit_auth_event("login_failed", user=user, scope=scope)
        routed_response = self._route_authenticated_user()
        if routed_response is not None:
            return routed_response

        if (
            self._request_portal_mode() == "internal"
            and not request.session.uid
        ):
            return self._render_internal_login(response)
        return response

    @http.route()
    def login_successful_external_user(self, **kwargs):
        routed_response = self._route_authenticated_user()
        if routed_response is not None:
            return routed_response
        return super().login_successful_external_user(**kwargs)


class SotufabSession(Session):

    @http.route()
    def logout(self, redirect="/web"):
        uid = request.session.uid
        user = request.env["res.users"].sudo().browse(uid).exists() if uid else False
        if uid:
            _log_auth_event("logout", user=user)
        return super().logout(redirect=redirect)


class SotufabWebsiteHome(WebsiteController):

    @http.route("/", auth="public", website=True, sitemap=False)
    def index(self, **kwargs):
        if request.website.company_id.name == "Sotufab Distribution Tunisie":
            return request.redirect("/shop", code=302)
        return super().index(**kwargs)
