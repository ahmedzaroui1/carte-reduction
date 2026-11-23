import re

from odoo import api, models
from odoo.exceptions import ValidationError


PASSWORD_POLICY_MESSAGE = (
    "Le mot de passe doit contenir au moins 8 caractères, "
    "une majuscule (A-Z) et un chiffre (0-9)."
)


def validate_password_policy(password):
    if (
        not isinstance(password, str)
        or len(password) < 8
        or not re.search(r"[A-Z]", password)
        or not re.search(r"[0-9]", password)
    ):
        raise ValidationError(PASSWORD_POLICY_MESSAGE)


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if (
                "password" in values
                and values["password"] is not False
                and values["password"] is not None
            ):
                validate_password_policy(values["password"])
        return super().create(vals_list)

    def write(self, values):
        if (
            "password" in values
            and values["password"] is not False
            and values["password"] is not None
        ):
            validate_password_policy(values["password"])
        return super().write(values)
