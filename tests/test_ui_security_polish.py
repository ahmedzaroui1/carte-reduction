import re
import uuid
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.cartes_reduction.models.res_users import (
    PASSWORD_POLICY_MESSAGE,
)


class TestPasswordAndEmailPolish(TransactionCase):

    def test_password_policy_applies_to_user_creation_and_changes(self):
        users = self.env["res.users"].with_context(skip_card_eval=True)
        base_values = {
            "name": "Password policy test",
            "login": f"password-policy-{uuid.uuid4().hex}",
            "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])],
        }
        for password in ("", "Short1A", "lowercase123", "NoDigitsHere"):
            with self.subTest(password=password):
                with self.assertRaisesRegex(
                    ValidationError,
                    re.escape(PASSWORD_POLICY_MESSAGE),
                ):
                    users.create({**base_values, "password": password})

        user = users.create({**base_values, "password": "StrongPass123"})
        user.write({"password": "AnotherStrong4"})
        with self.assertRaisesRegex(
            ValidationError,
            re.escape(PASSWORD_POLICY_MESSAGE),
        ):
            user.write({"password": "weak"})

    def test_loyalty_email_uses_branded_art_for_all_tiers(self):
        company = self.env["res.company"].search([
            ("name", "=", "Sotufab Distribution Tunisie"),
        ], limit=1) or self.env.company
        partner = self.env["res.partner"].with_company(company).create({
            "name": '<img src=x onerror="alert(1)">',
            "email": "loyalty-art-test@example.com",
        })
        expected_assets = {
            "bronze": "card-bronze-2.png",
            "silver": "card-silver-5.png",
            "gold": "card-gold-vip-10.png",
            "platinum": "card-platinum-16.png",
        }

        for tier, asset in expected_assets.items():
            card = self.env["cartes.reduction"].search([
                ("carte_type", "=", tier),
                ("active", "=", True),
            ], limit=1)
            self.assertTrue(card, f"Missing active {tier} card")
            body = partner.with_company(company)._render_loyalty_email(
                "Test fidélité",
                "Votre compte a été mis à jour.",
                card=card,
            )
            self.assertIn(asset, body)
            self.assertIn(
                f"/logo.png?company={company.id}",
                body,
            )
            self.assertIn("Sotufab Distribution Tunisie", body)
            self.assertIn("&lt;img", body)
            self.assertNotIn('<img src=x onerror="alert(1)">', body)

    def test_invoice_email_has_only_relevant_branded_content(self):
        company = self.env["res.company"].search([
            ("name", "=", "Sotufab Distribution Tunisie"),
        ], limit=1) or self.env.company
        tnd = self.env.ref("base.TND")
        self.assertEqual(tnd.decimal_places, 3)
        if company.currency_id == tnd:
            tax = company.account_sale_tax_id
            self.assertEqual(tax.amount, 19.0)
            self.assertTrue(tax.price_include)

        invoice_template = self.env.ref("account.email_template_edi_invoice")
        self.assertEqual(invoice_template.lang, "fr_FR")
        self.assertIn("Votre facture", invoice_template.subject)
        report_view = self.env.ref("cartes_reduction.report_invoice_french")
        self.assertIn("fr_FR", report_view.arch_db)

        posted_invoice = self.env["account.move"].search([
            ("company_id", "=", company.id),
            ("move_type", "=", "out_invoice"),
            ("state", "=", "posted"),
            ("amount_tax", ">", 0),
        ], order="id desc", limit=1)
        if posted_invoice:
            report_html, _content_type = self.env.ref(
                "account.account_invoices"
            )._render_qweb_html(
                "account.report_invoice",
                posted_invoice.ids,
            )
            report_html = (
                report_html.decode("utf-8")
                if isinstance(report_html, bytes)
                else report_html
            )
            self.assertIn(posted_invoice.name, report_html)
            self.assertIn("TVA", report_html)

        recipient = self.env["res.partner"].with_context(
            skip_card_eval=True
        ).create({
            "name": '<img src=x onerror="alert(1)">',
            "email": "invoice-mail-test@example.com",
        })
        invoice = self.env["account.move"].new({
            "name": "INV/TEST/001",
            "company_id": company.id,
        })

        body = invoice._render_cartes_invoice_email(recipient)
        self.assertIn('lang="fr"', body)
        self.assertIn("Votre facture INV/TEST/001", body)
        self.assertIn("TVA applicable", body)
        self.assertIn("&lt;img", body)
        self.assertNotIn("/static/img/cards/", body)
        self.assertEqual(len(re.findall(r"<img\b", body)), 1)

    def test_public_users_cannot_read_product_association_rules(self):
        public_user = self.env.ref("base.public_user")
        with self.assertRaises(AccessError):
            self.env["product.association"].with_user(public_user).search([])

    def test_order_discount_requires_active_card_and_matching_cart(self):
        company = self.env["res.company"].search([
            ("name", "=", "Sotufab Distribution Tunisie"),
        ], limit=1) or self.env.company
        partner = self.env["res.partner"].with_context(
            skip_card_eval=True
        ).create({"name": "Association rule test customer"})
        bronze = self.env["cartes.reduction"].search([
            ("carte_type", "=", "bronze"),
            ("active", "=", True),
        ], limit=1)
        gold = self.env["cartes.reduction"].search([
            ("carte_type", "=", "gold"),
            ("active", "=", True),
        ], limit=1)
        self.assertTrue(bronze)
        self.assertTrue(gold)
        self.env["cartes.attribution"].create({
            "client_id": partner.id,
            "carte_ids": [(6, 0, bronze.ids)],
            "state": "validated",
            "date_expiration": fields.Date.today() + timedelta(days=30),
        })

        products = self.env["product.template"].create([
            {"name": "Association antecedent", "sale_ok": True},
            {"name": "Association suggested product", "sale_ok": True},
        ]).mapped("product_variant_id")
        order = self.env["sale.order"].with_company(company).create({
            "partner_id": partner.id,
            "company_id": company.id,
        })
        for product in products:
            self.env["sale.order.line"].create({
                "order_id": order.id,
                "product_id": product.id,
                "name": product.display_name,
                "product_uom": product.uom_id.id,
                "product_uom_qty": 1,
                "price_unit": product.lst_price,
            })
        rule = self.env["product.association"].create({
            "antecedent_product_ids": [(6, 0, products[:1].ids)],
            "associated_product_id": products[1].id,
            "confidence": 80,
            "support": 20,
            "card_id": bronze.id,
        })

        self.assertTrue(order._get_association_reward(rule))
        rule.write({"card_id": gold.id})
        self.assertFalse(order._get_association_reward(rule))
        rule.write({"card_id": bronze.id, "active": False})
        self.assertFalse(order._get_association_reward(rule))


@tagged("post_install", "-at_install")
class TestPortalShopRedirect(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param(
            "auth_signup.invitation_scope",
            "b2c",
        )
        cls.portal_login = f"shop-redirect-{uuid.uuid4().hex}"
        cls.env["res.users"].with_context(skip_card_eval=True).create({
            "name": "Shop redirect test customer",
            "login": cls.portal_login,
            "password": "StrongPass123",
            "groups_id": [(6, 0, [cls.env.ref("base.group_portal").id])],
        })

    def test_authenticated_portal_default_landing_is_shop(self):
        self.authenticate(self.portal_login, "StrongPass123")
        response = self.url_open(
            "/web/login",
            allow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers["Location"].endswith("/shop"))

    def test_signup_route_renders_password_policy_error_in_french(self):
        response = self.url_open("/web/signup")
        self.assertEqual(response.status_code, 200)
        csrf = re.search(
            rb'name="csrf_token"\s+value="([^"]+)"',
            response.content,
        )
        self.assertTrue(csrf)
        response = self.url_open(
            "/web/signup",
            data={
                "csrf_token": csrf.group(1).decode(),
                "login": f"weak-password-{uuid.uuid4().hex}",
                "name": "Weak password test",
                "password": "weak",
                "confirm_password": "weak",
                "redirect": "",
                "token": "",
            },
            allow_redirects=False,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(PASSWORD_POLICY_MESSAGE, response.text)
