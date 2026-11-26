from odoo import http
from odoo.addons.auth_signup.controllers.main import AuthSignupHome
from odoo.addons.website.controllers.main import Website as WebsiteController
from odoo.addons.web.controllers.session import Session as WebSession
from odoo.exceptions import AccessDenied
from odoo.http import request

from ..models.auth_audit import log_auth_event


def _audit_auth_event(event_type, user=None, login=None, actor_uid=None, details=None):
    if user:
        login = login or user.login
        actor_uid = user.id
    return log_auth_event(
        request.db,
        event_type,
        login=login,
        actor_uid=actor_uid,
        details=details,
        user_agent_env=request.httprequest.environ,
    )


def _audit_current_logout():
    uid = request.session.uid
    if not uid:
        return
    user = request.env["res.users"].sudo().browse(uid).exists()
    if user:
        _audit_auth_event(
            "logout",
            user=user,
            details="Déconnexion via %s" % request.httprequest.path,
        )


class SotufabAuthSignupHome(AuthSignupHome):

    def _request_portal_mode(self):
        return request.httprequest.headers.get("X-Sotufab-Portal")

    def _reject_portal_login(self):
        uid = request.session.uid
        if uid:
            user = request.env["res.users"].sudo().browse(uid).exists()
            if user:
                _audit_auth_event(
                    "login_failed",
                    user=user,
                    details="Accès refusé sur ce portail",
                )
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

    def _render_staff_login(self, error=None, login=""):
        return request.render(
            "cartes_reduction.internal_staff_login",
            {
                "error": error,
                "login": login,
            },
        )

    def _route_staff_user(self, user):
        if user.has_group("base.group_portal"):
            _audit_auth_event(
                "login_failed",
                user=user,
                details="Compte réservé à la boutique en ligne",
            )
            request.session.logout(keep_db=True)
            return self._render_staff_login(
                "Accès refusé : compte réservé à la boutique en ligne"
            )

        is_manager = user.has_group(
            "base.group_system"
        ) or user.has_group("cartes_reduction.cartes_manager_group")
        if user.has_group("point_of_sale.group_pos_user") and not is_manager:
            return request.redirect("/pos/ui", 303)
        if user.has_group("base.group_user"):
            return request.redirect("/web", 303)

        _audit_auth_event(
            "login_failed",
            user=user,
            details="Compte non autorisé sur le portail interne",
        )
        request.session.logout(keep_db=True)
        return self._render_staff_login("Accès refusé : compte non autorisé")

    @http.route(
        "/staff/login",
        type="http",
        auth="none",
        methods=["GET", "POST"],
        csrf=True,
        website=False,
        sitemap=False,
    )
    def staff_login(self, **kwargs):
        if request.session.uid:
            user = request.env["res.users"].sudo().browse(request.session.uid)
            if user.exists():
                return self._route_staff_user(user)
            request.session.logout(keep_db=True)

        if request.httprequest.method == "POST":
            login = (request.params.get("login") or "").strip()
            password = request.params.get("password") or ""
            if not request.db:
                return self._render_staff_login("Base de données indisponible", login)

            try:
                uid = request.session.authenticate(request.db, login, password)
            except AccessDenied:
                return self._render_staff_login("Identifiants invalides", login)

            if uid:
                if request.session.uid != uid:
                    _audit_auth_event(
                        "login_failed",
                        login=login,
                        actor_uid=uid,
                        details="Validation multifacteur non terminée",
                    )
                    request.session.logout(keep_db=True)
                    return self._render_staff_login(
                        "Une validation multifacteur supplémentaire est requise"
                    )
                user = request.env["res.users"].sudo().browse(uid)
                if user.exists():
                    response = self._route_staff_user(user)
                    if request.session.uid == uid:
                        _audit_auth_event("login", user=user)
                    return response
                request.session.logout(keep_db=True)

            _audit_auth_event(
                "login_failed",
                login=login,
                details="Authentification non aboutie",
            )
            return self._render_staff_login("Identifiants invalides", login)

        return self._render_staff_login()

    @http.route()
    def web_login(self, *args, **kwargs):
        previous_uid = request.session.uid
        response = super().web_login(*args, **kwargs)
        routed_response = self._route_authenticated_user()
        uid = request.session.uid
        if uid and uid != previous_uid:
            user = request.env["res.users"].sudo().browse(uid).exists()
            if user:
                _audit_auth_event("login", user=user)
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


class SotufabWebsiteHome(WebsiteController):

    @http.route("/", auth="public", website=True, sitemap=False)
    def index(self, **kwargs):
        if request.website.company_id.name == "Sotufab Distribution Tunisie":
            return request.redirect("/shop", code=302)
        return super().index(**kwargs)


class SotufabSessionAudit(WebSession):

    @http.route()
    def authenticate(self, db, login, password, base_location=None):
        result = super().authenticate(db, login, password, base_location)
        uid = request.session.uid
        if uid and result.get("uid") == uid:
            user = request.env["res.users"].sudo().browse(uid).exists()
            if user:
                _audit_auth_event("login", user=user)
        return result

    @http.route()
    def destroy(self):
        _audit_current_logout()
        return super().destroy()

    @http.route()
    def logout(self, redirect="/web"):
        _audit_current_logout()
        return super().logout(redirect)
