from datetime import datetime, time

from odoo import api, fields, models
from odoo.exceptions import ValidationError

class CarteCondition(models.Model):
    _name = 'cartes.conditions'
    _description = "Conditions d'Obtention des Cartes"

    name = fields.Char(string="Nom de la Condition", required=True)
    description = fields.Text(string="Description de la Condition")
    active = fields.Boolean(string="Active", default=True)
    min_amount = fields.Monetary(
        string="Achats minimum ce mois",
        currency_field="currency_id",
        help="Dépenses minimales du client pendant le mois calendaire en cours.",
    )
    min_points = fields.Float(string="Points de fidélité minimum")
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )

    @api.constrains("min_amount", "min_points")
    def _check_thresholds(self):
        for condition in self:
            if condition.min_amount < 0 or condition.min_points < 0:
                raise ValidationError("Les seuils ne peuvent pas être négatifs.")

    def is_eligible(self, partner):
        """Check that the partner meets every configured threshold."""
        if self.env.context.get("skip_card_eval"):
            return False
        self.ensure_one()
        if not self.active:
            return False

        monthly_spend = self._get_monthly_spend(partner)
        loyalty_points = self._get_loyalty_points(partner)
        return (
            monthly_spend >= self.min_amount
            and loyalty_points >= self.min_points
        )

    def _get_monthly_spend(self, partner):
        if self.env.context.get("skip_card_eval"):
            return 0.0
        today = fields.Date.context_today(self)
        period_start = fields.Date.subtract(today, days=30)
        start_datetime = datetime.combine(period_start, time.min)
        end_datetime = datetime.combine(fields.Date.add(today, days=1), time.min)
        commercial_partner = partner.commercial_partner_id
        company = self.env.company
        total = 0.0

        pos_orders = self.env["pos.order"].search([
            ("partner_id", "child_of", commercial_partner.id),
            ("date_order", ">=", start_datetime),
            ("date_order", "<", end_datetime),
            ("state", "in", ["paid", "done", "invoiced"]),
        ])
        sale_orders = self.env["sale.order"].search([
            ("partner_id", "child_of", commercial_partner.id),
            ("date_order", ">=", start_datetime),
            ("date_order", "<", end_datetime),
            ("state", "in", ["sale", "done"]),
        ])

        for order in list(pos_orders) + list(sale_orders):
            order_date = order.date_order.date() if order.date_order else today
            total += order.currency_id._convert(
                order.amount_total,
                self.currency_id,
                company,
                order_date,
            )
        return total

    def _get_loyalty_points(self, partner):
        if self.env.context.get("skip_card_eval"):
            return 0.0
        cards = self.env["loyalty.card"].search([
            ("partner_id", "child_of", partner.commercial_partner_id.id),
            ("program_id.program_type", "=", "loyalty"),
            "|",
            ("expiration_date", "=", False),
            ("expiration_date", ">=", fields.Date.context_today(self)),
        ])
        return sum(cards.mapped("points"))
