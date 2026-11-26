from odoo import api, fields, models
from odoo.exceptions import AccessError


LOYALTY_POINTS_PROGRAM_NAME = "Cartes de Réduction - Points TND"
LOYALTY_TIER_RANK = {
    "bronze": 0,
    "standard": 1,
    "silver": 2,
    "gold": 3,
    "premium": 4,
    "platinum": 5,
}
LOYALTY_TIER_LABELS = {
    "bronze": "Bronze",
    "standard": "Standard",
    "silver": "Silver",
    "gold": "Gold",
    "premium": "Premium",
    "platinum": "Platinum",
}


class ResPartnerAdminSummary(models.Model):
    _inherit = "res.partner"

    active_loyalty_card_name = fields.Char(
        string="Carte de fidélité active",
        compute="_compute_admin_loyalty_summary",
        compute_sudo=True,
        groups="base.group_system",
    )
    active_loyalty_tier = fields.Char(
        string="Palier",
        compute="_compute_admin_loyalty_summary",
        compute_sudo=True,
        groups="base.group_system",
    )
    loyalty_points_balance = fields.Float(
        string="Solde de points",
        compute="_compute_loyalty_points_balance",
        compute_sudo=True,
        groups="base.group_system",
    )
    churn_risk_level = fields.Selection(
        [
            ("low", "Faible"),
            ("medium", "Modéré"),
            ("high", "Élevé"),
        ],
        string="Risque de désertion",
        compute="_compute_churn_risk_level",
        groups="base.group_system",
    )

    @api.depends(
        "cartes_attribution_ids.state",
        "cartes_attribution_ids.date_expiration",
        "cartes_attribution_ids.carte_ids.active",
        "cartes_attribution_ids.carte_ids.carte_type",
    )
    def _compute_admin_loyalty_summary(self):
        today = fields.Date.context_today(self)
        for partner in self:
            attributions = self.env["cartes.attribution"].sudo().search([
                ("client_id", "child_of", partner.commercial_partner_id.id),
                ("state", "in", ["validated", "renouvelé"]),
                "|",
                ("date_expiration", "=", False),
                ("date_expiration", ">=", today),
            ])
            cards = attributions.mapped("carte_ids").filtered(
                lambda card: card.active
                and card.carte_type in LOYALTY_TIER_RANK
            )
            card = cards.sorted(
                key=lambda item: (
                    LOYALTY_TIER_RANK[item.carte_type],
                    item.id,
                )
            )[-1:]
            partner.active_loyalty_card_name = card.display_name if card else False
            partner.active_loyalty_tier = (
                LOYALTY_TIER_LABELS[card.carte_type] if card else False
            )

    def _compute_loyalty_points_balance(self):
        today = fields.Date.context_today(self)
        programs = self.env["loyalty.program"].sudo().search([
            ("name", "=", LOYALTY_POINTS_PROGRAM_NAME),
            ("program_type", "=", "loyalty"),
            ("active", "=", True),
            ("company_id", "in", [False, self.env.company.id]),
        ])
        for partner in self:
            if not programs:
                partner.loyalty_points_balance = 0.0
                continue
            cards = self.env["loyalty.card"].sudo().search([
                ("partner_id", "child_of", partner.commercial_partner_id.id),
                ("program_id", "in", programs.ids),
                "|",
                ("expiration_date", "=", False),
                ("expiration_date", ">=", today),
            ])
            partner.loyalty_points_balance = sum(cards.mapped("points"))

    @api.depends("churn_probability")
    def _compute_churn_risk_level(self):
        for partner in self:
            probability = partner.churn_probability or 0.0
            if probability >= 0.66:
                partner.churn_risk_level = "high"
            elif probability >= 0.33:
                partner.churn_risk_level = "medium"
            else:
                partner.churn_risk_level = "low"


class CartesCinAdminOnly(models.AbstractModel):
    _inherit = "cartes.reduction.cin.mixin"

    def action_reveal_cin(self):
        if not self.env.user.has_group("base.group_system"):
            raise AccessError("La révélation de la CIN est réservée à l'administrateur.")
        action = super().action_reveal_cin()
        params = action.get("params")
        if params:
            params["title"] = "Révélation de la CIN"
        return action
