from odoo import http
from odoo.addons.auth_signup.controllers.main import AuthSignupHome
from odoo.http import request


class SotufabAuthSignupHome(AuthSignupHome):

    @http.route()
    def web_login(self, *args, **kwargs):
        response = super().web_login(*args, **kwargs)
        if request.session.uid and not request.params.get("redirect"):
            user = request.env["res.users"].sudo().browse(request.session.uid)
            if user.has_group("base.group_portal") and not user.has_group(
                "base.group_user"
            ):
                return request.redirect("/shop", 303)
        return response

    @http.route()
    def login_successful_external_user(self, **kwargs):
        user = request.env.user
        if user.has_group("base.group_portal") and not user.has_group(
            "base.group_user"
        ):
            return request.redirect("/shop", 303)
        return super().login_successful_external_user(**kwargs)
