from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

from .security_utils import (
    cin_blind_index,
    decrypt_cin,
    encrypt_cin,
    log_security_event,
    mask_cin,
)


_CIN_ADMIN_GROUPS = (
    "base.group_system,cartes_reduction.cartes_manager_group"
)


class CartesCinSecurityMixin(models.AbstractModel):
    _name = "cartes.reduction.cin.mixin"
    _description = "Encrypted Tunisian CIN fields"

    cin_encrypted = fields.Char(
        copy=False,
        groups=_CIN_ADMIN_GROUPS,
        readonly=True,
    )
    cin_hash = fields.Char(
        copy=False,
        index=True,
        groups=_CIN_ADMIN_GROUPS,
        readonly=True,
    )
    cin_masked = fields.Char(
        string="CIN",
        compute="_compute_cin_masked",
        compute_sudo=True,
    )
    cin = fields.Char(
        string="Nouvelle CIN",
        compute="_compute_cin_input",
        inverse="_inverse_cin",
        groups=_CIN_ADMIN_GROUPS,
    )

    _sql_constraints = [
        ("cin_hash_unique", "unique(cin_hash)", "This CIN is already registered."),
    ]

    @api.depends("cin_encrypted")
    def _compute_cin_masked(self):
        for record in self:
            ciphertext = record.sudo().cin_encrypted
            record.cin_masked = mask_cin(decrypt_cin(ciphertext))

    @api.depends("cin_hash")
    def _compute_cin_input(self):
        for record in self:
            record.cin = False

    def _inverse_cin(self):
        for record in self:
            raw_cin = (record.cin or "").strip()
            if raw_cin:
                digest = cin_blind_index(raw_cin)
                duplicate = record.sudo().search_count([
                    ("cin_hash", "=", digest),
                    ("id", "!=", record.id or 0),
                ])
                if duplicate:
                    raise ValidationError("This CIN is already registered.")
                values = {
                    "cin_encrypted": encrypt_cin(raw_cin),
                    "cin_hash": digest,
                }
            else:
                values = {"cin_encrypted": False, "cin_hash": False}
            record.sudo().with_context(skip_card_eval=True).write(values)
            record.invalidate_recordset(["cin"])

    def action_reveal_cin(self):
        self.ensure_one()
        if not (
            self.env.user.has_group("base.group_system")
            or self.env.user.has_group(
                "cartes_reduction.cartes_manager_group"
            )
        ):
            raise AccessError("Only security administrators may reveal a CIN.")
        raw_cin = decrypt_cin(self.sudo().cin_encrypted)
        if not raw_cin:
            raise ValidationError("No CIN is stored for this record.")

        model_name = self._name
        log_security_event(
            self.env,
            "cin_reveal",
            partner_id=self.id if model_name == "res.partner" else None,
            client_record_id=self.id if model_name == "cartes.client" else None,
            details=f"model={model_name}",
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "CIN revealed",
                "message": raw_cin,
                "type": "warning",
                "sticky": True,
            },
        }

