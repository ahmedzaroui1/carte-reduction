from odoo import fields, http
from odoo.http import request


class DiscountCardPortal(http.Controller):

    @http.route("/my/cartes", type="http", auth="user", website=True)
    def portal_discount_cards(self, **kwargs):
        return self._render_discount_cards()

    @http.route(
        "/my/cartes/otp",
        type="http",
        auth="user",
        website=True,
        methods=["POST"],
        csrf=True,
    )
    def portal_send_pos_otp(self, **kwargs):
        partner = request.env.user.partner_id.commercial_partner_id.sudo()
        issued = partner._issue_pos_otp()
        return self._render_discount_cards(issued)

    def _render_discount_cards(self, issued_otp=None):
        partner = request.env.user.partner_id.commercial_partner_id
        today = fields.Date.context_today(request.env.user)
        attributions = request.env["cartes.attribution"].sudo().search([
            ("client_id", "child_of", partner.id),
            ("state", "in", ["validated", "renouvelé"]),
            ("date_expiration", ">=", today),
        ])
        cards = attributions.mapped("carte_ids").filtered("active")
        promotions = request.env["cartes.promotions"].sudo().search([
            ("carte_id", "in", cards.ids),
            ("state", "=", "active"),
        ])

        tier_order = {
            "bronze": 0,
            "standard": 1,
            "silver": 2,
            "gold": 3,
            "premium": 4,
        }
        current_rank = max(
            (tier_order.get(card.carte_type, -1) for card in cards),
            default=-1,
        )
        next_cards = request.env["cartes.reduction"].sudo().search([
            ("active", "=", True),
            ("conditions_ids", "!=", False),
        ]).filtered(
            lambda card: card not in cards
            and bool(card.conditions_ids.filtered("active"))
            and tier_order.get(card.carte_type, -1) > current_rank
        )
        next_tiers = [{
            "card": card,
            "conditions": card.conditions_ids.filtered("active"),
            "eligible": card.is_eligible_for(partner),
        } for card in next_cards]
        points_program = request.env["loyalty.program"].sudo().search([
            ("name", "=", "Cartes de Réduction - Points TND"),
            ("active", "=", True),
            ("company_id", "in", [False, request.env.company.id]),
        ], limit=1)
        loyalty_cards = request.env["loyalty.card"].sudo().search([
            ("partner_id", "child_of", partner.id),
            ("program_id", "=", points_program.id),
            "|",
            ("expiration_date", "=", False),
            ("expiration_date", ">=", today),
        ]) if points_program else request.env["loyalty.card"]
        points_balance = sum(loyalty_cards.mapped("points"))
        active_conditions = request.env["cartes.conditions"].sudo().search([
            ("active", "=", True),
        ])
        spend_conditions = active_conditions.filtered("min_amount")
        monthly_spend = float(
            (spend_conditions[0]._get_monthly_spend(partner) if spend_conditions else 0)
            or 0.0
        )
        unread_notifications = request.env["notifications.client"].sudo().search([
            ("client_id", "child_of", partner.id),
            ("is_read", "=", False),
        ], order="date desc", limit=20)
        progress = {
            "silver_spend": min(100, 100 * monthly_spend / 300) if monthly_spend else 0,
            "silver_points": min(100, 100 * points_balance / 600) if points_balance else 0,
            "gold_spend": min(100, 100 * monthly_spend / 1000) if monthly_spend else 0,
            "gold_points": min(100, 100 * points_balance / 1500) if points_balance else 0,
        }

        return request.render("cartes_reduction.portal_discount_cards", {
            "attributions": attributions,
            "cards": cards,
            "promotions": promotions,
            "next_tiers": next_tiers,
            "points_balance": points_balance,
            "monthly_spend": monthly_spend,
            "unread_notifications": unread_notifications,
            "progress": progress,
            "otp_code": issued_otp and issued_otp["code"],
            "otp_qr_data_uri": issued_otp and issued_otp["qr_data_uri"],
            "otp_expires_epoch": issued_otp and issued_otp["expires_epoch"],
        })
