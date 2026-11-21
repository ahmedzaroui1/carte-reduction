from email.message import EmailMessage
import json
import smtplib
import uuid
from urllib.error import URLError
from urllib.request import urlopen
from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase


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
            "reward_point_amount": 1.0,
            "minimum_amount": 0.0,
            "active": True,
        }
        if rule:
            rule.write(rule_values)
        else:
            self.env["loyalty.rule"].create(rule_values)

        reward = program.reward_ids.filtered(
            lambda record: record.reward_type == "discount"
            and record.discount_mode == "per_order"
            and record.required_points == 100
        )[:1]
        reward_values = {
            "program_id": program.id,
            "reward_type": "discount",
            "discount_mode": "per_order",
            "discount": 5.0,
            "required_points": 100.0,
            "discount_applicability": "order",
            "description": "5 TND de remise pour 100 points",
            "active": True,
        }
        if reward:
            reward.write(reward_values)
        else:
            self.env["loyalty.reward"].create(reward_values)
        return program

    def test_partner_creation_assigns_welcome_bronze_once(self):
        bronze = self.env["cartes.reduction"].search([
            ("carte_type", "=", "bronze"),
            ("active", "=", True),
        ], order="id", limit=1)
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

    def test_one_tnd_awards_one_point_and_upgrades_at_600(self):
        tnd = self.env.ref("base.TND")
        program = self._get_points_program()
        points_reward = program.reward_ids.filtered(
            lambda record: record.reward_type == "discount"
            and record.discount_mode == "per_order"
            and record.required_points == 100
        )[:1]
        self.assertAlmostEqual(points_reward.discount, 5.0)
        partner = self.env["res.partner"].with_context(skip_card_eval=True).create({
            "name": "Pass 1 points upgrade test",
        }).with_context(skip_card_eval=False)
        points_card = self.env["loyalty.card"].create({
            "partner_id": partner.id,
            "program_id": program.id,
            "points": 599.0,
        })

        bronze = self.env["cartes.reduction"].create({
            "name": "Pass 1 Bronze",
            "carte_type": "bronze",
            "active": True,
        })
        silver_condition = self.env["cartes.conditions"].create({
            "name": "Pass 1 Silver at 600 points",
            "min_amount": 0.0,
            "min_points": 600.0,
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
            "discount": 10.0,
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
            "name": "Pass 1 one TND product",
            "list_price": 1.0,
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
            "price_unit": 1.0,
            "tax_id": [(5, 0, 0)],
        })
        self.assertAlmostEqual(order.amount_total, 1.0)

        with patch.object(type(self.env["mail.mail"]), "send"):
            self.assertAlmostEqual(partner._process_card_purchase(order), 1.0)
            partner._process_card_purchase(order)

        self.assertAlmostEqual(points_card.points, 600.0)
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
