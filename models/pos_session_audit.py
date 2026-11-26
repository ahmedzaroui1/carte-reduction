from odoo import models
from odoo.http import request


class PosSessionAudit(models.Model):
    _inherit = "pos.session"

    def write(self, vals):
        previous_states = {
            session.id: session.state for session in self
        } if "state" in vals else {}
        result = super().write(vals)
        if "state" not in vals:
            return result

        for session in self:
            previous_state = previous_states.get(session.id)
            if previous_state == session.state:
                continue
            if session.state == "opened":
                event_type = "pos_session_opened"
                action = "Ouverture"
            elif session.state == "closed":
                event_type = "pos_session_closed"
                action = "Fermeture"
            else:
                continue

            user = self.env.user
            if user.has_group("base.group_system"):
                role = "Administrateur"
            elif user.has_group("point_of_sale.group_pos_manager"):
                role = "Gestionnaire POS"
            else:
                role = "Caissier POS"
            try:
                remote_ip = request.httprequest.environ.get("REMOTE_ADDR")
            except RuntimeError:
                remote_ip = False
            details = (
                "Canal : Caisse POS | Action : %s | Session : %s | "
                "Configuration : %s | Utilisateur : %s | Rôle RBAC : %s | IP : %s"
            ) % (
                action,
                session.name or session.id,
                session.config_id.display_name,
                user.name,
                role,
                remote_ip or "inconnue",
            )
            self.env["cartes.reduction.security.audit"].sudo().create({
                "event_type": event_type,
                "user_id": user.id,
                "details": details,
            })
        return result
