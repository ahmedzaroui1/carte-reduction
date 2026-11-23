from email.message import EmailMessage
import json
import smtplib
import uuid
from urllib.error import URLError
from urllib.request import urlopen
from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase

from odoo.addons.cartes_reduction.hooks import configure_pass1_data


POINTS_PROGRAM_NAME = "Cartes de Réduction - Points TND"


class TestPass1Loyalty(TransactionCase):

    def _get_points_program(self):
        tnd = self.env.ref("base.TND")
        program = self.env["loyalty.program"].search([
            ("name", "=", POINTS_PROGRAM_NAME),
            ("company_id", "=", self.env.company.id),
        ], limit=1)
        if not program:
            program = self.env["loyalty.program"].create({
                "name": POINTS_PROGRAM_NAME,
                "program_type": "loyalty",
                "trigger": "auto",
                "currency_id": tnd.id,
                "company_id": self.env.company.id,
                "active": True,
            })
        rule = program.rule_ids.filtered(
            lambda record: record.reward_point_mode == "money"
        )[:1]
        rule_values = {
            "program_id": program.id,
            "reward_point_mode": "money",
            "reward_point_amount": 0.1,
            "minimum_amount": 0.0,
            "active": True,
        }
        if rule:
            rule.write(rule_values)
        else:
            self.env["loyalty.rule"].create(rule_values)

        for required_points, amount, description in (
            (500.0, 10.0, "10 TND de remise pour 500 points"),
            (1500.0, 35.0, "35 TND de remise pour 1 500 points"),
        ):
            reward = program.reward_ids.filtered(
                lambda record: (
                    record.reward_type == "discount"
                    and record.discount_mode == "per_order"
                    and record.required_points == required_points
                )
            )[:1]
            reward_values = {
                "program_id": program.id,
                "reward_type": "discount",
                "discount_mode": "per_order",
                "discount": amount,
                "required_points": required_points,
                "discount_applicability": "order",
                "description": description,
                "active": True,
            }
            if reward:
                reward.write(reward_values)
            else:
                self.env["loyalty.reward"].create(reward_values)
        for reward in program.reward_ids.filtered(
            lambda record: (
                record.active
                and record.reward_type == "discount"
                and record.discount_mode == "per_order"
            )
        ):
            if reward.required_points not in {500.0, 1500.0}:
                reward.write({"active": False})
        return program

    def test_partner_creation_assigns_welcome_bronze_once(self):
        configure_pass1_data(self.env)
        bronze = self.env["cartes.reduction"].search([
            ("carte_type", "=", "bronze"),
            ("active", "=", True),
        ], order="id", limit=1)
        bronze.write({"discount": 0.0, "percent": 0.0})
        self.assertTrue(bronze)

        with patch.object(type(self.env["mail.mail"]), "send") as send_mail:
            partner = self.env["res.partner"].create({
                "name": "Pass 1 welcome card test",
                "email": "pass1-welcome@example.com",
            })
            partner._assign_welcome_bronze()

        attribution = self.env["cartes.attribution"].search([
            ("client_id", "=", partner.id),
            ("carte_ids", "in", bronze.ids),
        ])
        self.assertEqual(len(attribution), 1)
        self.assertEqual(attribution.state, "validated")
        self.assertEqual(attribution.reference, f"CARD-{attribution.id:06d}")
        welcome_mail = self.env["mail.mail"].search([
            ("email_to", "=", partner.email),
            ("mail_message_id.subject", "ilike", "Bienvenue"),
        ], limit=1)
        self.assertIn(partner.name, welcome_mail.body_html)
        self.assertIn(attribution.reference, welcome_mail.body_html)
        points_program = partner._get_points_program()
        self.assertEqual(self.env["loyalty.card"].search_count([
            ("partner_id", "=", partner.commercial_partner_id.id),
            ("program_id", "=", points_program.id),
        ]), 1)
        self.assertEqual(
            self.env["notifications.client"].search_count([
                ("client_id", "=", partner.id),
                ("notification_type", "=", "welcome"),
            ]),
            1,
        )
        send_mail.assert_called_once()

    def test_partner_family_keeps_only_one_highest_tier_attribution(self):
        root = self.env["res.partner"].with_context(skip_card_eval=True).create({
            "name": "Pass 1 card family root",
        })
        child = self.env["res.partner"].with_context(skip_card_eval=True).create({
            "name": "Pass 1 card family contact",
            "parent_id": root.id,
            "type": "delivery",
        })
        active_cards = self.env["cartes.reduction"].search([
            ("carte_type", "in", ["bronze", "silver", "gold"]),
            ("active", "=", True),
        ], order="id")
        cards_by_type = {
            card.carte_type: card
            for card in active_cards
        }
        self.assertEqual(set(cards_by_type), {"bronze", "silver", "gold"})
        today = fields.Date.context_today(self.env["res.partner"])
        attributions = self.env["cartes.attribution"].with_context(
            skip_card_eval=True
        )
        for client, card in (
            (root, cards_by_type["bronze"]),
            (child, cards_by_type["bronze"]),
            (root, cards_by_type["silver"]),
            (root, cards_by_type["gold"]),
        ):
            attributions.create({
                "client_id": client.id,
                "carte_ids": [(6, 0, [card.id])],
                "state": "validated",
                "date_attribution": today,
                "date_expiration": fields.Date.add(today, days=30),
            })

        with patch.object(type(self.env["mail.mail"]), "send") as send_mail:
            kept = child.with_context(skip_card_eval=False)._assign_welcome_bronze()
            self.env["res.partner"].create({
                "name": "Pass 1 additional family contact",
                "parent_id": root.id,
                "type": "delivery",
            })

        family_attributions = self.env["cartes.attribution"].search([
            ("client_id", "child_of", root.id),
            ("state", "in", ["validated", "renouvelé"]),
            ("date_expiration", ">=", today),
        ])
        self.assertEqual(kept.carte_ids.carte_type, "gold")
        self.assertEqual(len(family_attributions), 1)
        self.assertEqual(family_attributions.carte_ids.carte_type, "gold")
        self.assertEqual(
            self.env["cartes.attribution"].search_count([
                ("client_id", "child_of", root.id),
                ("carte_ids", "in", cards_by_type["bronze"].ids),
                ("state", "=", "expired"),
            ]),
            2,
        )
        send_mail.assert_not_called()

    def test_enterprise_tier_rates_conditions_and_point_rewards(self):
        configure_pass1_data(self.env)
        configure_pass1_data(self.env)
        cards = {
            tier: self.env["cartes.reduction"].search([
                ("carte_type", "=", tier),
                ("active", "=", True),
            ], order="id", limit=1)
            for tier in ("bronze", "silver", "gold", "platinum")
        }
        self.assertEqual(
            {tier: card.discount for tier, card in cards.items()},
            {"bronze": 2.0, "silver": 5.0, "gold": 10.0, "platinum": 16.0},
        )
        self.assertTrue(cards["gold"].is_vip)
        self.assertTrue(cards["platinum"].is_vip)
        self.assertTrue(cards["bronze"].image_1920)
        self.assertTrue(cards["bronze"].image)
        self.assertEqual(cards["bronze"].conditions_ids.mapped("min_amount"), [0.0])
        self.assertEqual(cards["bronze"].conditions_ids.mapped("min_points"), [0.0])

        expected_conditions = {
            "silver": {(40000.0, 0.0), (0.0, 4000.0)},
            "gold": {(60000.0, 0.0), (0.0, 6000.0)},
            "platinum": {(100000.0, 0.0), (0.0, 10000.0)},
        }
        for tier, expected in expected_conditions.items():
            self.assertEqual(
                {
                    (condition.min_amount, condition.min_points)
                    for condition in cards[tier].conditions_ids
                },
                expected,
            )
            program = cards[tier].loyalty_program_id
            reward = program.reward_ids.filtered(
                lambda item: item.reward_type == "discount"
            )
            self.assertEqual(len(reward), 1)
            self.assertEqual(reward.discount, cards[tier].discount)

        partner = self.env["res.partner"].with_context(
            skip_card_eval=True
        ).create({"name": "Silver alternative eligibility"})
        with (
            patch.object(
                type(cards["silver"].conditions_ids),
                "_get_monthly_spend",
                return_value=40000.0,
            ),
            patch.object(
                type(cards["silver"].conditions_ids),
                "_get_loyalty_points",
                return_value=0.0,
            ),
        ):
            self.assertTrue(cards["silver"].is_eligible_for(partner))
        with (
            patch.object(
                type(cards["gold"].conditions_ids),
                "_get_monthly_spend",
                return_value=0.0,
            ),
            patch.object(
                type(cards["gold"].conditions_ids),
                "_get_loyalty_points",
                return_value=6000.0,
            ),
        ):
            self.assertTrue(cards["gold"].is_eligible_for(partner))

        points_program = self._get_points_program()
        self.assertEqual(
            points_program.rule_ids.filtered(
                lambda rule: rule.reward_point_mode == "money"
            )[:1].reward_point_amount,
            0.1,
        )
        points_rewards = points_program.reward_ids.filtered(
            lambda reward: (
                reward.active
                and reward.reward_type == "discount"
                and reward.discount_mode == "per_order"
            )
        ).sorted("required_points")
        self.assertEqual(
            [(reward.required_points, reward.discount) for reward in points_rewards],
            [(500.0, 10.0), (1500.0, 35.0)],
        )

    def test_purchase_sends_one_combined_points_and_upgrade_email(self):
        configure_pass1_data(self.env)
        partner = self.env["res.partner"].with_context(
            skip_card_eval=True
        ).create({
            "name": "Combined loyalty summary customer",
            "email": "combined-loyalty@example.com",
        }).with_context(skip_card_eval=False)
        points_program = self._get_points_program()
        points_card = self.env["loyalty.card"].create({
            "partner_id": partner.id,
            "program_id": points_program.id,
            "points": 3999.0,
        })
        bronze = self.env["cartes.reduction"].search([
            ("carte_type", "=", "bronze"),
            ("active", "=", True),
        ], limit=1)
        bronze.write({"discount": 0.0, "percent": 0.0})
        today = fields.Date.context_today(self.env["res.partner"])
        self.env["cartes.attribution"].create({
            "client_id": partner.id,
            "carte_ids": [(6, 0, bronze.ids)],
            "state": "validated",
            "date_attribution": today,
            "date_expiration": fields.Date.add(today, days=30),
        })
        pricelist = self.env["product.pricelist"].create({
            "name": "Combined loyalty TND pricelist",
            "currency_id": self.env.ref("base.TND").id,
        })
        product = self.env["product.template"].create({
            "name": "Combined loyalty one TND product",
            "list_price": 10.204081632653,
            "taxes_id": [(5, 0, 0)],
        }).product_variant_id
        order = self.env["sale.order"].create({
            "partner_id": partner.id,
            "pricelist_id": pricelist.id,
        })
        self.env["sale.order.line"].create({
            "order_id": order.id,
            "product_id": product.id,
            "product_uom_qty": 1.0,
            "price_unit": 10.204081632653,
            "tax_id": [(5, 0, 0)],
        })
        self.assertAlmostEqual(order.amount_total, 10.0)

        with patch.object(type(self.env["mail.mail"]), "send") as send_mail:
            partner._process_card_purchase(order)

        self.assertAlmostEqual(points_card.points, 4000.0)
        self.assertEqual(send_mail.call_count, 1)
        summary = self.env["mail.mail"].search([
            ("email_to", "=", partner.email),
            ("mail_message_id.subject", "ilike", "évolue vers Silver"),
        ], limit=1)
        self.assertTrue(summary)
        self.assertIn("4000.00", summary.body_html)
        self.assertIn("/my/cartes", summary.body_html)
        self.assertIn("Silver", summary.body_html)
        self.assertEqual(
            self.env["notifications.client"].search_count([
                ("client_id", "=", partner.id),
                ("notification_type", "=", "card_upgrade"),
            ]),
            1,
        )
        self.assertFalse(self.env["notifications.client"].search([
            ("client_id", "=", partner.id),
            ("notification_type", "=", "points_awarded"),
        ]))

    def test_ten_tnd_awards_one_point_and_upgrades_at_4000(self):
        configure_pass1_data(self.env)
        tnd = self.env.ref("base.TND")
        program = self._get_points_program()
        points_reward = program.reward_ids.filtered(
            lambda record: record.reward_type == "discount"
            and record.discount_mode == "per_order"
            and record.required_points == 500
        )[:1]
        self.assertAlmostEqual(points_reward.discount, 10.0)
        partner = self.env["res.partner"].with_context(skip_card_eval=True).create({
            "name": "Pass 1 points upgrade test",
        }).with_context(skip_card_eval=False)
        points_card = self.env["loyalty.card"].create({
            "partner_id": partner.id,
            "program_id": program.id,
            "points": 3999.0,
        })

        bronze = self.env["cartes.reduction"].search([
            ("carte_type", "=", "bronze"),
            ("active", "=", True),
        ], order="id", limit=1)
        silver_condition = self.env["cartes.conditions"].create({
            "name": "Pass 1 Silver at 4,000 points",
            "min_amount": 0.0,
            "min_points": 4000.0,
            "currency_id": tnd.id,
        })
        silver = self.env["cartes.reduction"].search([
            ("carte_type", "=", "silver"),
            ("active", "=", True),
        ], order="id", limit=1)
        if not silver:
            silver = self.env["cartes.reduction"].create({
                "name": "Pass 1 Silver",
                "carte_type": "silver",
                "active": True,
            })
        silver.write({
            "discount": 5.0,
            "conditions_ids": [(6, 0, silver_condition.ids)],
        })
        today = fields.Date.context_today(self.env["res.partner"])
        bronze_attribution = self.env["cartes.attribution"].create({
            "client_id": partner.id,
            "carte_ids": [(6, 0, bronze.ids)],
            "state": "validated",
            "date_attribution": today,
            "date_expiration": fields.Date.add(today, days=30),
        })

        pricelist = self.env["product.pricelist"].create({
            "name": "Pass 1 TND test pricelist",
            "currency_id": tnd.id,
        })
        product = self.env["product.template"].create({
            "name": "Pass 1 ten TND product",
            "list_price": 10.204081632653,
            "taxes_id": [(5, 0, 0)],
        }).product_variant_id
        order = self.env["sale.order"].create({
            "partner_id": partner.id,
            "pricelist_id": pricelist.id,
        })
        self.env["sale.order.line"].create({
            "order_id": order.id,
            "product_id": product.id,
            "product_uom_qty": 1.0,
            "price_unit": 10.204081632653,
            "tax_id": [(5, 0, 0)],
        })
        self.assertAlmostEqual(order.amount_total, 10.0)

        with patch.object(type(self.env["mail.mail"]), "send"):
            self.assertAlmostEqual(partner._process_card_purchase(order), 1.0)
            partner._process_card_purchase(order)

        self.assertAlmostEqual(points_card.points, 4000.0)
        self.assertTrue(order.loyalty_points_processed)
        self.assertEqual(bronze_attribution.state, "expired")
        silver_attribution = self.env["cartes.attribution"].search([
            ("client_id", "=", partner.id),
            ("carte_ids", "in", silver.ids),
            ("state", "=", "validated"),
        ])
        self.assertEqual(len(silver_attribution), 1)

    def test_configured_mailpit_receives_odoo_mail(self):
        address = f"pass1-{uuid.uuid4().hex}@example.com"
        mail_server = self.env["ir.mail_server"].search([
            ("name", "=", "Mailpit"),
            ("active", "=", True),
        ], limit=1)
        self.assertTrue(mail_server, "The Mailpit outgoing server is not configured.")
        message = EmailMessage()
        message["From"] = "odoo@example.com"
        message["To"] = address
        message["Subject"] = "Pass 1 Mailpit delivery check"
        message.set_content("Mailpit delivery test")
        smtp = smtplib.SMTP(mail_server.smtp_host, mail_server.smtp_port, timeout=5)
        try:
            smtp.sendmail("odoo@example.com", [address], message.as_string())
        finally:
            smtp.quit()

        url = "http://mailpit:8025/api/v1/messages?limit=100"
        try:
            with urlopen(url, timeout=5) as response:
                result = json.loads(response.read().decode("utf-8"))
        except URLError as error:
            self.fail(f"Mailpit API is unavailable: {error}")
        delivered = any(
            recipient.get("Address") == address
            for message in result.get("messages", [])
            for recipient in message.get("To", [])
        )
        self.assertTrue(
            delivered,
            f"The message was not present in the Mailpit inbox: {result.get('messages', [])}",
        )
