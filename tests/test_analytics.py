from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase


class TestOmnichannelAnalytics(TransactionCase):

    def setUp(self):
        super().setUp()
        self.partner = self.env["res.partner"].with_context(
            skip_card_eval=True
        ).create({
            "name": "Omnichannel analytics customer",
            "email": "analytics-customer@example.com",
        })

    def test_purchase_stats_use_confirmed_orders_across_partner_family(self):
        child = self.env["res.partner"].with_context(
            skip_card_eval=True
        ).create({
            "name": "Omnichannel analytics child contact",
            "parent_id": self.partner.id,
            "type": "delivery",
        })
        today = fields.Datetime.to_datetime(fields.Datetime.now())
        pos_order = self.env["pos.order"].new({
            "partner_id": child.id,
            "date_order": today - timedelta(days=10),
            "state": "paid",
        })
        sale_order = self.env["sale.order"].new({
            "partner_id": child.id,
            "date_order": today,
            "state": "sale",
        })
        self.partner.total_sales = 200.0

        with (
            patch.object(
                type(self.env["pos.order"]),
                "search",
                return_value=pos_order,
            ) as pos_search,
            patch.object(
                type(self.env["sale.order"]),
                "search",
                return_value=sale_order,
            ) as sale_search,
        ):
            self.partner._compute_purchase_stats()

        self.assertEqual(self.partner.purchase_order_count, 2)
        self.assertEqual(self.partner.avg_purchase_value, 100.0)
        self.assertEqual(self.partner.purchase_frequency, 10.0)
        self.assertEqual(
            self.partner.last_purchase_date,
            fields.Date.to_date(today),
        )
        self.assertIn(
            ("partner_id", "child_of", self.partner.id),
            pos_search.call_args.args[0],
        )
        self.assertIn(
            ("partner_id", "child_of", self.partner.id),
            sale_search.call_args.args[0],
        )

    def test_segment_color_mapping_and_twelve_month_clv(self):
        colors = {
            "vip": "success",
            "regular": "info",
            "new": "warning",
            "churned": "danger",
        }
        for segment, color in colors.items():
            self.partner.customer_segment = segment
            self.partner._compute_segment_color()
            self.assertEqual(self.partner.segment_color, color)

        self.assertAlmostEqual(
            self.partner._calculate_clv(SimpleNamespace(
                purchase_order_count=2,
                avg_purchase_value=100.0,
                purchase_frequency=30.0,
                churn_probability=0.25,
            )),
            100.0 * (365.0 / 30.0) * 0.75,
        )

        one_order_partner = SimpleNamespace(
            purchase_order_count=1,
            avg_purchase_value=120.0,
            churn_probability=0.5,
        )
        self.assertEqual(self.partner._calculate_clv(one_order_partner), 60.0)

        prospect = SimpleNamespace(purchase_order_count=0)
        self.assertEqual(self.partner._calculate_clv(prospect), 0.0)

    def test_ml_predictions_do_not_forecast_for_zero_order_prospects(self):
        prospect = self.env["res.partner"].new({
            "purchase_order_count": 0,
            "total_sales": 0.0,
        })
        model = SimpleNamespace(
            predict=lambda _features: self.fail("model should not forecast prospects")
        )
        with patch.object(type(prospect), "_get_ml_model", return_value=model):
            prospect._predict_ml_metrics()

        self.assertEqual(prospect.predicted_clv, 0.0)
        self.assertEqual(prospect.churn_probability, 0.9)
        self.assertFalse(prospect.next_purchase_prediction)

    def test_ai_refresh_is_limited_to_system_administrators(self):
        public_user = self.env.ref("base.public_user")
        with self.assertRaises(AccessError):
            self.partner.with_user(public_user).action_refresh_ai_predictions()

    def test_customer_dashboard_exposes_only_business_measures(self):
        fields = self.env["res.partner"].fields_get(
            [
                "total_sales",
                "predicted_clv",
                "avg_purchase_value",
                "churn_probability",
                "color",
                "partner_latitude",
                "partner_longitude",
                "partner_gid",
                "pos_otp_attempts",
                "rfm_score",
                "customer_rank",
                "supplier_rank",
                "purchase_order_count",
                "purchase_frequency",
                "recency_score",
                "frequency_score",
                "monetary_score",
                "message_bounce",
                "debit_limit",
            ],
            ["type", "group_operator"],
        )
        self.assertEqual(fields["total_sales"]["group_operator"], "sum")
        self.assertEqual(fields["predicted_clv"]["group_operator"], "sum")
        self.assertEqual(fields["avg_purchase_value"]["group_operator"], "avg")
        self.assertEqual(fields["churn_probability"]["group_operator"], "avg")
        for technical_field in (
            "color",
            "partner_latitude",
            "partner_longitude",
            "partner_gid",
            "pos_otp_attempts",
            "rfm_score",
            "customer_rank",
            "supplier_rank",
            "purchase_order_count",
            "purchase_frequency",
            "recency_score",
            "frequency_score",
            "monetary_score",
            "message_bounce",
            "debit_limit",
        ):
            self.assertNotIn("group_operator", fields[technical_field])

    def test_apriori_historical_data_includes_sale_and_pos_transactions(self):
        products = self.env["product.product"].search([], limit=2)
        if len(products) < 2:
            products = self.env["product.template"].create([
                {"name": "Analytics Apriori product A"},
                {"name": "Analytics Apriori product B"},
            ]).mapped("product_variant_id")
        pos_order = SimpleNamespace(lines=[
            SimpleNamespace(product_id=products[0], _fields={}),
            SimpleNamespace(product_id=products[1], _fields={}),
        ])
        sale_order = SimpleNamespace(order_line=[
            SimpleNamespace(product_id=products[0], _fields={}),
            SimpleNamespace(product_id=products[1], _fields={}),
        ])
        generator = self.env["product.association.generator"]

        with (
            patch.object(
                type(self.env["pos.order"]),
                "search",
                return_value=[pos_order],
            ),
            patch.object(
                type(self.env["sale.order"]),
                "search",
                return_value=[sale_order],
            ),
        ):
            transactions = generator._get_historical_data()

        expected = [str(products[0].id), str(products[1].id)]
        self.assertEqual(transactions, [expected, expected])

    def test_discount_usage_counts_orders_and_ignores_expired_tiers(self):
        today = fields.Date.context_today(self.partner)
        bronze = self.env["cartes.reduction"].create({
            "name": "Expired analytics Bronze",
            "carte_type": "bronze",
            "active": True,
            "state": "active",
        })
        gold = self.env["cartes.reduction"].create({
            "name": "Active analytics Gold",
            "carte_type": "gold",
            "active": True,
            "state": "active",
        })
        attributions = self.env["cartes.attribution"].with_context(
            skip_card_eval=True
        )
        attributions.create({
            "client_id": self.partner.id,
            "carte_ids": [(6, 0, bronze.ids)],
            "state": "validated",
            "date_attribution": fields.Date.subtract(today, days=60),
            "date_expiration": fields.Date.subtract(today, days=1),
        })
        attributions.create({
            "client_id": self.partner.id,
            "carte_ids": [(6, 0, gold.ids)],
            "state": "validated",
            "date_attribution": today,
            "date_expiration": fields.Date.add(today, days=30),
        })
        product = self.env["product.template"].create({
            "name": "Analytics discounted sale product",
            "list_price": 100.0,
            "taxes_id": [(6, 0, [])],
        }).product_variant_id
        order = self.env["sale.order"].with_context(skip_card_eval=True).create({
            "partner_id": self.partner.id,
            "date_order": fields.Datetime.now(),
            "state": "sale",
            "applied_discount_card_id": gold.id,
            "order_line": [
                (0, 0, {
                    "product_id": product.id,
                    "product_uom_qty": 1,
                    "price_unit": 100.0,
                    "discount": 10.0,
                }),
                (0, 0, {
                    "product_id": product.id,
                    "product_uom_qty": 1,
                    "price_unit": 100.0,
                    "discount": 10.0,
                }),
            ],
        })
        order.flush_recordset()
        order.order_line.flush_recordset()

        stats = self.env["carte.usage.stats"].search([
            ("partner_id", "=", self.partner.id),
        ])
        self.assertEqual(len(stats), 1)
        self.assertEqual(stats.carte_id, gold)
        self.assertEqual(stats.total_usage, 1)
        self.assertEqual(stats.sale_usage, 1)
        self.assertEqual(stats.website_usage, 0)

        product_sales = self.env["dashboard.product.sales"].search([
            ("product_id", "=", product.id),
        ])
        self.assertEqual(len(product_sales), 1)
        self.assertEqual(product_sales.total_qty, 2.0)
        self.assertAlmostEqual(product_sales.total_revenue, 180.0)

    def test_sql_dashboard_views_expose_unique_record_ids(self):
        purchases = self.env["dashboard.purchase.by.customer"].search([])
        product_sales = self.env["dashboard.product.sales"].search([])
        card_usage = self.env["carte.usage.stats"].search([])
        for records in (purchases, product_sales, card_usage):
            ids = records.ids
            self.assertEqual(len(ids), len(set(ids)))
