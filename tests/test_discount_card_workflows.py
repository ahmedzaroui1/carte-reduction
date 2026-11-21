import pandas as pd
from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase


class TestDiscountCardWorkflows(TransactionCase):

    def setUp(self):
        super().setUp()
        self.partner = self.env["res.partner"].with_context(skip_card_eval=True).create({
            "name": "Discount Card Workflow Test",
            "email": "discount-card-workflow@example.com",
        })

    def test_assignment_uses_tier_dates_and_notifies_vip_once(self):
        today = fields.Date.today()
        tier = self.env["cartes.reduction"].create({
            "name": "Workflow VIP Tier",
            "is_vip": True,
            "date_debut": today,
            "date_expiration": fields.Date.add(today, days=30),
        })
        attribution = self.env["cartes.attribution"].create({
            "client_id": self.partner.id,
            "carte_ids": [(6, 0, tier.ids)],
        })

        self.assertEqual(attribution.date_attribution, today)
        self.assertEqual(attribution.date_expiration, tier.date_expiration)

        with patch.object(type(self.env["mail.mail"]), "send"):
            attribution.action_attribuer()
            attribution.action_attribuer()

        self.assertEqual(attribution.state, "validated")
        self.assertEqual(tier.nombre_utilisateurs, 1)
        self.assertEqual(
            self.env["notifications.client"].search_count([
                ("client_id", "=", self.partner.id),
                ("notification_type", "=", "vip"),
            ]),
            1,
        )

    def test_expiry_notification_has_valid_type(self):
        attribution = self.env["cartes.attribution"].create({
            "client_id": self.partner.id,
            "carte_ids": [(6, 0, self.env["cartes.reduction"].create({
                "name": "Workflow Expiry Tier",
            }).ids)],
            "date_expiration": fields.Date.subtract(fields.Date.today(), days=1),
            "state": "validated",
        })

        with patch.object(type(self.env["mail.mail"]), "send"):
            attribution._notify_expired_cards()

        self.assertEqual(attribution.state, "expired")
        notification = self.env["notifications.client"].search([
            ("client_id", "=", self.partner.id),
            ("notification_type", "=", "expired_card"),
        ], limit=1)
        self.assertTrue(notification)

    def test_condition_thresholds_and_card_alternative_conditions(self):
        condition = self.env["cartes.conditions"].create({
            "name": "Monthly spend query test",
        })
        self.assertEqual(condition._get_monthly_spend(self.partner), 0)

        amount_and_points = self.env["cartes.conditions"].create({
            "name": "Monthly spend and points",
            "min_amount": 100,
            "min_points": 20,
        })
        high_spend = self.env["cartes.conditions"].create({
            "name": "Higher monthly spend",
            "min_amount": 200,
        })
        tier = self.env["cartes.reduction"].create({
            "name": "Workflow Silver Tier",
            "conditions_ids": [(6, 0, (amount_and_points | high_spend).ids)],
        })

        with (
            patch.object(type(amount_and_points), "_get_monthly_spend", return_value=100),
            patch.object(type(amount_and_points), "_get_loyalty_points", return_value=19),
        ):
            self.assertFalse(amount_and_points.is_eligible(self.partner))
            self.assertFalse(tier.is_eligible_for(self.partner))

        with (
            patch.object(type(amount_and_points), "_get_monthly_spend", return_value=100),
            patch.object(type(amount_and_points), "_get_loyalty_points", return_value=20),
        ):
            self.assertTrue(tier.is_eligible_for(self.partner))

        with (
            patch.object(type(amount_and_points), "_get_monthly_spend", return_value=200),
            patch.object(type(amount_and_points), "_get_loyalty_points", return_value=0),
        ):
            self.assertTrue(tier.is_eligible_for(self.partner))

    def test_promotion_state_filters_follow_dates(self):
        today = fields.Date.today()
        promotions = self.env["cartes.promotions"].create([
            {
                "name": "Current workflow promotion",
                "date_debut": fields.Date.subtract(today, days=1),
                "date_expiration": fields.Date.add(today, days=1),
            },
            {
                "name": "Planned workflow promotion",
                "date_debut": fields.Date.add(today, days=1),
            },
            {
                "name": "Expired workflow promotion",
                "date_expiration": fields.Date.subtract(today, days=1),
            },
        ])

        self.assertEqual(promotions[0].state, "active")
        self.assertEqual(promotions[1].state, "draft")
        self.assertEqual(promotions[2].state, "expired")
        self.assertIn(promotions[0], self.env["cartes.promotions"].search([("state", "=", "active")]))
        self.assertIn(promotions[1], self.env["cartes.promotions"].search([("state", "=", "draft")]))

    def test_association_rules_resolve_catalog_product_ids(self):
        product_a = self.env["product.template"].create({
            "name": "Workflow association product A",
        }).product_variant_id
        product_b = self.env["product.template"].create({
            "name": "Workflow association product B",
        }).product_variant_id
        generator = self.env["product.association.generator"]
        rules = pd.DataFrame([{
            "antecedents": frozenset([str(product_a.id)]),
            "consequents": frozenset([str(product_b.id)]),
            "confidence": 0.8,
            "support": 0.2,
        }])

        self.assertEqual(generator._save_rules_to_odoo(rules), 1)
        saved = self.env["product.association"].search([
            ("associated_product_id", "=", product_b.id),
        ])
        self.assertEqual(saved.antecedent_product_ids, product_a)

        empty_rules = pd.DataFrame(columns=rules.columns)
        self.assertEqual(generator._save_rules_to_odoo(empty_rules), 0)
        self.assertTrue(saved.exists())

    def test_sales_amounts_compute_without_a_loyalty_reward(self):
        product = self.env["product.template"].create({
            "name": "Workflow sales product",
            "sale_ok": True,
            "list_price": 100,
        }).product_variant_id
        order = self.env["sale.order"].create({
            "partner_id": self.partner.id,
        })
        self.env["sale.order.line"].create({
            "order_id": order.id,
            "product_id": product.id,
            "product_uom_qty": 1,
            "price_unit": 100,
        })

        order._compute_amounts()

        self.assertEqual(order.amount_untaxed, 100)

    def test_sales_amounts_apply_available_card_discount(self):
        product = self.env["product.template"].create({
            "name": "Workflow discounted sales product",
            "sale_ok": True,
            "list_price": 100,
        }).product_variant_id
        order = self.env["sale.order"].create({
            "partner_id": self.partner.id,
        })
        line = self.env["sale.order.line"].create({
            "order_id": order.id,
            "product_id": product.id,
            "product_uom_qty": 1,
            "price_unit": 100,
        })
        loyalty_info = {
            "max_discount": 15,
            "best_reward": self.env["loyalty.reward"],
        }

        with patch.object(type(line), "_get_loyalty_info", return_value=loyalty_info):
            order._compute_amounts()

        self.assertEqual(line.discount, 15)
        self.assertEqual(order.amount_untaxed, 85)
