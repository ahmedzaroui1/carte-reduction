from odoo import models
from odoo.http import request


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _dispatch(cls, endpoint):
        portal = request.httprequest.headers.get("X-Sotufab-Portal")
        uid = request.session.uid
        path = request.httprequest.path

        if uid and portal in ("public", "internal"):
            user = request.env["res.users"].sudo().browse(uid)
            if not user.exists():
                request.session.logout(keep_db=True)
                return request.make_response(
                    "Accès refusé",
                    headers=[("Content-Type", "text/plain; charset=utf-8")],
                    status=403,
                )

            if portal == "public" and user.has_group("base.group_user"):
                return request.make_response(
                    "Accès réservé au portail interne",
                    headers=[("Content-Type", "text/plain; charset=utf-8")],
                    status=403,
                )

            if (
                portal == "internal"
                and user.has_group("base.group_portal")
                and path != "/staff/login"
            ):
                return request.redirect("/staff/login", 303)

        return super()._dispatch(endpoint)
