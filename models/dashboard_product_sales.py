from odoo import models, fields, tools

class DashboardProductSales(models.Model):
    _name = 'dashboard.product.sales'
    _description = 'Ventes globales des produits (POS + Sale Order)'
    _auto = False  # Vue SQL matérialisée

    product_id = fields.Many2one('product.product', string="Produit", readonly=True)
    product_name = fields.Char(string="Nom du Produit", readonly=True)
    product_categ_id = fields.Many2one('product.category', string="Catégorie", readonly=True)
    total_qty = fields.Float(string="Quantité Totale Vendue", readonly=True)
    currency_id = fields.Many2one('res.currency', string="Devise", readonly=True)
    total_revenue = fields.Monetary(
        string="Chiffre d'Affaires (TND)",
        currency_field="currency_id",
        readonly=True,
    )
    average_price = fields.Monetary(
        string="Prix Moyen (TND)",
        currency_field="currency_id",
        readonly=True,
    )

    def init(self):
        tools.drop_view_if_exists(self._cr, self._table)
        self._cr.execute("""
            CREATE OR REPLACE VIEW dashboard_product_sales AS
            WITH RECURSIVE company_roots AS (
                SELECT id, id AS root_id
                FROM res_company
                WHERE parent_id IS NULL

                UNION ALL

                SELECT child.id, parent.root_id
                FROM res_company child
                JOIN company_roots parent ON child.parent_id = parent.id
            ),
            sales_lines AS (
                SELECT
                    sol.product_id,
                    so.company_id,
                    company_roots.root_id,
                    company.currency_id AS target_currency_id,
                    so.currency_id AS source_currency_id,
                    so.date_order::date AS order_date,
                    sol.product_uom_qty AS quantity,
                    sol.price_subtotal AS subtotal
                FROM sale_order_line sol
                JOIN sale_order so ON so.id = sol.order_id
                JOIN res_company company ON company.id = so.company_id
                JOIN company_roots ON company_roots.id = so.company_id
                WHERE so.state IN ('sale', 'done')
                  AND sol.product_id IS NOT NULL

                UNION ALL

                SELECT
                    pol.product_id,
                    po.company_id,
                    company_roots.root_id,
                    company.currency_id AS target_currency_id,
                    company.currency_id AS source_currency_id,
                    po.date_order::date AS order_date,
                    pol.qty AS quantity,
                    pol.price_subtotal AS subtotal
                FROM pos_order_line pol
                JOIN pos_order po ON po.id = pol.order_id
                JOIN res_company company ON company.id = po.company_id
                JOIN company_roots ON company_roots.id = po.company_id
                WHERE po.state IN ('paid', 'done', 'invoiced')
                  AND pol.product_id IS NOT NULL
            ),
            converted_lines AS (
                SELECT
                    sales_lines.*,
                    sales_lines.subtotal
                        * COALESCE(target_rate.rate, 1.0)
                        / NULLIF(COALESCE(source_rate.rate, 1.0), 0.0) AS revenue
                FROM sales_lines
                LEFT JOIN LATERAL (
                    SELECT COALESCE(
                        (
                            SELECT rate.rate
                            FROM res_currency_rate rate
                            WHERE rate.currency_id = sales_lines.source_currency_id
                              AND rate.name <= sales_lines.order_date
                              AND (rate.company_id IS NULL OR rate.company_id = sales_lines.root_id)
                            ORDER BY rate.company_id, rate.name DESC
                            LIMIT 1
                        ),
                        (
                            SELECT rate.rate
                            FROM res_currency_rate rate
                            WHERE rate.currency_id = sales_lines.source_currency_id
                              AND (rate.company_id IS NULL OR rate.company_id = sales_lines.root_id)
                            ORDER BY rate.company_id, rate.name ASC
                            LIMIT 1
                        ),
                        1.0
                    ) AS rate
                ) source_rate ON TRUE
                LEFT JOIN LATERAL (
                    SELECT COALESCE(
                        (
                            SELECT rate.rate
                            FROM res_currency_rate rate
                            WHERE rate.currency_id = sales_lines.target_currency_id
                              AND rate.name <= sales_lines.order_date
                              AND (rate.company_id IS NULL OR rate.company_id = sales_lines.root_id)
                            ORDER BY rate.company_id, rate.name DESC
                            LIMIT 1
                        ),
                        (
                            SELECT rate.rate
                            FROM res_currency_rate rate
                            WHERE rate.currency_id = sales_lines.target_currency_id
                              AND (rate.company_id IS NULL OR rate.company_id = sales_lines.root_id)
                            ORDER BY rate.company_id, rate.name ASC
                            LIMIT 1
                        ),
                        1.0
                    ) AS rate
                ) target_rate ON TRUE
            )
            SELECT
                ROW_NUMBER() OVER (
                    ORDER BY converted_lines.product_id, converted_lines.target_currency_id
                ) AS id,
                converted_lines.product_id,
                COALESCE(pt.name ->> 'fr_FR', pt.name ->> 'en_US', '') AS product_name,
                pt.categ_id AS product_categ_id,
                converted_lines.target_currency_id AS currency_id,
                SUM(converted_lines.quantity) AS total_qty,
                SUM(converted_lines.revenue) AS total_revenue,
                CASE
                    WHEN SUM(converted_lines.quantity) > 0
                    THEN SUM(converted_lines.revenue) / SUM(converted_lines.quantity)
                    ELSE 0
                END AS average_price
            FROM converted_lines
            JOIN product_product pp ON pp.id = converted_lines.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            GROUP BY
                converted_lines.product_id,
                converted_lines.target_currency_id,
                pt.name,
                pt.categ_id
        """)
