from odoo import api, fields, models
from odoo.exceptions import ValidationError


class CartePromotion(models.Model):
    _name = 'cartes.promotions'
    _description = "Promotions des Cartes de Réduction"

    name = fields.Char(string="Nom de la Promotion", required=True)
    discount = fields.Float(string="Réduction (%)")
    carte_id = fields.Many2one('cartes.reduction', string="Carte Associée")
    loyalty_program_id = fields.Many2one('loyalty.program', string="Programme de Fidélité")
    pricelist_id = fields.Many2one('product.pricelist', string="Liste de Prix")
    date_debut = fields.Date(string="Date de Début")
    date_expiration = fields.Date(string="Date d'Expiration")
    state = fields.Selection(
        [
            ('draft', 'Planifiée'),
            ('active', 'En cours'),
            ('expired', 'Expirée'),
        ],
        string="Statut",
        compute="_compute_state",
        search="_search_state",
    )

    @api.depends("date_debut", "date_expiration")
    def _compute_state(self):
        today = fields.Date.context_today(self)
        for promotion in self:
            if promotion.date_expiration and promotion.date_expiration < today:
                promotion.state = "expired"
            elif promotion.date_debut and promotion.date_debut > today:
                promotion.state = "draft"
            else:
                promotion.state = "active"

    @api.model
    def _search_state(self, operator, value):
        domains = {
            "draft": [("date_debut", ">", fields.Date.context_today(self))],
            "active": [
                "&",
                "|", ("date_debut", "=", False), ("date_debut", "<=", fields.Date.context_today(self)),
                "|", ("date_expiration", "=", False), ("date_expiration", ">=", fields.Date.context_today(self)),
            ],
            "expired": [("date_expiration", "<", fields.Date.context_today(self))],
        }
        if operator == "=":
            return domains.get(value, [("id", "=", 0)])
        if operator == "!=":
            return [("id", "not in", self.search(domains.get(value, [("id", "=", 0)])).ids)]
        if operator in ("in", "not in"):
            selected = value if isinstance(value, (list, tuple)) else [value]
            matching_ids = []
            for state in selected:
                matching_ids.extend(self.search(domains.get(state, [("id", "=", 0)])).ids)
            if operator == "in":
                return [("id", "in", list(set(matching_ids)))]
            return [("id", "not in", list(set(matching_ids)))]
        raise NotImplementedError(f"Unsupported state search operator: {operator}")

    @api.constrains('date_debut', 'date_expiration')
    def _check_dates(self):
        for record in self:
            if record.date_debut and record.date_expiration and record.date_expiration < record.date_debut:
                raise ValidationError("La date d'expiration doit être après la date de début.")

    @api.constrains('discount')
    def _check_discount(self):
        for record in self:
            if record.discount < 0:
                raise ValidationError("La réduction ne peut pas être négative.")

