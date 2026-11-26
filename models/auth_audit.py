from odoo import SUPERUSER_ID, api, fields, models
from odoo.exceptions import AccessDenied
from odoo.modules.registry import Registry


AUTH_EVENT_TYPES = {"login", "logout", "login_failed"}


def log_auth_event(
    db,
    event_type,
    login=None,
    actor_uid=None,
    details=None,
    user_agent_env=None,
):
    if not db:
        raise ValueError("A database is required to record an authentication event.")
    if event_type not in AUTH_EVENT_TYPES:
        raise ValueError("Unsupported authentication audit event.")

    user_agent_env = user_agent_env or {}
    remote_ip = user_agent_env.get("REMOTE_ADDR")

    with Registry(db).cursor() as cr:
        env = api.Environment(cr, SUPERUSER_ID, {})
        users = env["res.users"]
        user = (
            users.browse(actor_uid).exists()
            if actor_uid
            else users.with_context(active_test=False).search(
                [("login", "=", login)], limit=1
            )
        )

        if not user:
            role = "Compte inconnu"
            user_name = "Utilisateur inconnu"
        elif user.has_group("base.group_system"):
            role = "Administrateur"
            user_name = user.name
        elif (
            user.has_group("point_of_sale.group_pos_user")
            and not user.has_group("point_of_sale.group_pos_manager")
        ):
            role = "Caissier POS restreint"
            user_name = user.name
        elif user.has_group("cartes_reduction.cartes_manager_group"):
            role = "Gestionnaire Cartes"
            user_name = user.name
        elif user.has_group("base.group_portal"):
            role = "Client Portail"
            user_name = user.name
        else:
            role = "Utilisateur interne"
            user_name = user.name

        def clean(value):
            return str(value or "").replace("|", "/").replace("\r", " ").replace("\n", " ").strip()

        detail_parts = [
            "Canal : Authentification",
            "Utilisateur : %s (login : %s)" % (clean(user_name), clean(login)),
            "Rôle RBAC : %s" % role,
            "IP : %s" % clean(remote_ip or "inconnue"),
        ]
        if details:
            detail_parts.append("Détail : %s" % clean(details))

        env["cartes.reduction.security.audit"].create(
            {
                "event_type": event_type,
                "user_id": user.id if user else False,
                "details": " | ".join(detail_parts),
            }
        )
        cr.commit()


class CartesReductionAuthAudit(models.Model):
    _inherit = "cartes.reduction.security.audit"

    event_type = fields.Selection(
        selection_add=[
            ("login", "Connexion"),
            ("logout", "Déconnexion"),
            ("login_failed", "Échec de connexion"),
            ("pos_session_opened", "Ouverture de session caisse POS"),
            ("pos_session_closed", "Fermeture de session caisse POS"),
        ],
        ondelete={
            "login": "cascade",
            "logout": "cascade",
            "login_failed": "cascade",
            "pos_session_opened": "cascade",
            "pos_session_closed": "cascade",
        },
    )

class ResUsersAuthAudit(models.Model):
    _inherit = "res.users"

    @classmethod
    def _login(cls, db, login, password, user_agent_env):
        try:
            return super()._login(db, login, password, user_agent_env)
        except AccessDenied:
            log_auth_event(
                db,
                "login_failed",
                login=login,
                details="Identifiants refusés",
                user_agent_env=user_agent_env,
            )
            raise
