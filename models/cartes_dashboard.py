from odoo import models, fields, api, tools

class CarteUsageStats(models.Model):
    _name = 'carte.usage.stats'
    _description = "Statistiques combinées POS et Ventes"
    _auto = False  # Vue SQL

    partner_id = fields.Many2one('res.partner', string="Client")
    carte_id = fields.Many2one('cartes.reduction', string="Carte")
    total_usage = fields.Integer(string="Utilisations Totales")
    pos_usage = fields.Integer(string="Utilisations POS")
    sale_usage = fields.Integer(string="Utilisations Ventes")
    website_usage = fields.Integer(string="Utilisations Web")
    last_use = fields.Date(string="Dernière utilisation")

    def init(self):
        tools.drop_view_if_exists(self._cr, self._table)
        self._cr.execute("""
            CREATE OR REPLACE VIEW carte_usage_stats AS
            WITH active_cards AS (
                SELECT DISTINCT
                    client.commercial_partner_id AS partner_id,
                    relation.cartes_reduction_id AS card_id
                FROM cartes_attribution attribution
                JOIN cartes_attribution_cartes_reduction_rel relation
                    ON relation.cartes_attribution_id = attribution.id
                JOIN cartes_reduction card ON card.id = relation.cartes_reduction_id
                JOIN res_partner client ON client.id = attribution.client_id
                WHERE attribution.state IN ('validated', 'renouvelé')
                  AND attribution.date_attribution <= CURRENT_DATE
                  AND (
                    attribution.date_expiration IS NULL
                    OR attribution.date_expiration >= CURRENT_DATE
                  )
                  AND card.active = TRUE
            ),
            pos_usage AS (
                SELECT
                    order_partner.commercial_partner_id AS partner_id,
                    pol.custom_discount_card_id AS card_id,
                    'pos' AS source,
                    COUNT(DISTINCT po.id) AS usage_count,
                    MAX(po.date_order::date) AS usage_date
                FROM pos_order po
                JOIN pos_order_line pol ON pol.order_id = po.id
                JOIN res_partner order_partner ON order_partner.id = po.partner_id
                JOIN active_cards
                    ON active_cards.partner_id = order_partner.commercial_partner_id
                   AND active_cards.card_id = pol.custom_discount_card_id
                WHERE po.state IN ('paid', 'done', 'invoiced')
                  AND pol.discount > 0
                GROUP BY order_partner.commercial_partner_id, pol.custom_discount_card_id
            ),
            sale_usage AS (
                SELECT
                    order_partner.commercial_partner_id AS partner_id,
                    so.applied_discount_card_id AS card_id,
                    CASE WHEN so.website_id IS NULL THEN 'sale' ELSE 'website' END AS source,
                    COUNT(DISTINCT so.id) AS usage_count,
                    MAX(so.date_order::date) AS usage_date
                FROM sale_order so
                JOIN sale_order_line sol ON sol.order_id = so.id
                JOIN res_partner order_partner ON order_partner.id = so.partner_id
                JOIN active_cards
                    ON active_cards.partner_id = order_partner.commercial_partner_id
                   AND active_cards.card_id = so.applied_discount_card_id
                WHERE so.state IN ('sale', 'done')
                  AND sol.discount > 0
                GROUP BY
                    order_partner.commercial_partner_id,
                    so.applied_discount_card_id,
                    CASE WHEN so.website_id IS NULL THEN 'sale' ELSE 'website' END
            ),
            combined_usage AS (
                SELECT * FROM pos_usage
                UNION ALL
                SELECT * FROM sale_usage
            )
            SELECT
                ROW_NUMBER() OVER (ORDER BY partner_id, card_id) AS id,
                partner_id,
                card_id AS carte_id,
                SUM(usage_count) AS total_usage,
                SUM(CASE WHEN source = 'pos' THEN usage_count ELSE 0 END) AS pos_usage,
                SUM(CASE WHEN source = 'sale' THEN usage_count ELSE 0 END) AS sale_usage,
                SUM(CASE WHEN source = 'website' THEN usage_count ELSE 0 END) AS website_usage,
                MAX(usage_date) AS last_use
            FROM combined_usage
            GROUP BY partner_id, card_id
        """)