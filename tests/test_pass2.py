from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase

from odoo.addons.cartes_reduction.hooks import (
    configure_pass1_data,
    configure_pass2_e2e,
    configure_tunisian_ttc_taxes,
)


class TestPass2Otp(TransactionCase):

    def setUp(self):
        super().setUp()
        self.partner = self.env["res.partner"].with_context(
            skip_card_eval=True
        ).create({
            "name": "Pass 2 OTP test customer",
            "email": "pass2-otp@example.com",
        })
        groups = self.env.ref("base.group_user") | self.env.ref(
            "point_of_sale.group_pos_user"
        )
        self.pos_user = self.env["res.users"].with_context(
            skip_card_eval=True
        ).create({
            "name": "Pass 2 OTP cashier",
            "login": "pass2-otp-cashier",
            "email": "pass2-cashier@example.com",
            "groups_id": [(6, 0, groups.ids)],
        })

    def test_pos_otp_is_one_time_and_qr_is_signed(self):
        with (
            patch(
                "odoo.addons.cartes_reduction.models.res_partner.secrets.randbelow",
                return_value=123456,
            ),
            patch.object(type(self.env["mail.mail"]), "send") as send_mail,
        ):
            issued = self.partner._issue_pos_otp()

        self.assertEqual(issued["code"], "123456")
        self.assertTrue(issued["qr_data_uri"].startswith("data:image/png;base64,"))
        self.assertNotEqual(self.partner.pos_otp_digest, issued["code"])
        otp_mail = self.env["mail.mail"].search([
            ("email_to", "=", self.partner.email),
            ("mail_message_id.subject", "=", "Votre code de vérification POS"),
        ], limit=1)
        self.assertIn("VOTRE CODE POS", otp_mail.body_html)
        self.assertIn("123456", otp_mail.body_html)
        self.assertIn("/my/cartes", otp_mail.body_html)
        self.assertEqual(
            self.partner.with_user(self.pos_user).verify_pos_otp_or_qr(
                issued["qr_token"]
            ),
            {"success": True, "message": "Client vérifié."},
        )
        replay = self.partner.with_user(self.pos_user).verify_pos_otp_or_qr(
            issued["qr_token"]
        )
        self.assertFalse(replay["success"])
        replay_event = self.env["cartes.reduction.security.audit"].search([
            ("event_type", "=", "replay_blocked"),
            ("partner_id", "=", self.partner.id),
        ], limit=1)
        self.assertTrue(replay_event)
        self.assertNotIn(issued["qr_token"], replay_event.details or "")
        self.assertEqual(send_mail.call_count, 1)

    def test_expired_unconsumed_otp_is_not_logged_as_replay(self):
        with (
            patch(
                "odoo.addons.cartes_reduction.models.res_partner.secrets.randbelow",
                return_value=123456,
            ),
            patch.object(type(self.env["mail.mail"]), "send"),
        ):
            issued = self.partner._issue_pos_otp()

        self.partner.sudo().write({
            "pos_otp_expires_at": fields.Datetime.now() - timedelta(seconds=61),
        })
        result = self.partner.with_user(self.pos_user).verify_pos_otp_or_qr(
            issued["code"]
        )

        self.assertFalse(result["success"])
        expired_event = self.env["cartes.reduction.security.audit"].search([
            ("event_type", "=", "otp_expired"),
            ("partner_id", "=", self.partner.id),
        ], limit=1)
        replay_event = self.env["cartes.reduction.security.audit"].search([
            ("event_type", "=", "replay_blocked"),
            ("partner_id", "=", self.partner.id),
        ], limit=1)
        self.assertTrue(expired_event)
        self.assertFalse(replay_event)

    def test_pos_otp_limits_failed_attempts_and_requires_pos_group(self):
        with (
            patch(
                "odoo.addons.cartes_reduction.models.res_partner.secrets.randbelow",
                return_value=123456,
            ),
            patch.object(type(self.env["mail.mail"]), "send"),
        ):
            self.partner._issue_pos_otp()

        for _attempt in range(3):
            result = self.partner.with_user(self.pos_user).verify_pos_otp_or_qr("000000")
        self.assertFalse(result["success"])
        self.assertEqual(self.partner.pos_otp_attempts, 3)
        self.assertTrue(self.partner.pos_otp_locked_until)
        with self.assertRaises(AccessError):
            self.partner.with_user(self.env.ref("base.public_user")).action_send_pos_otp()

    def test_pos_cashier_can_send_loyalty_notifications(self):
        bronze = self.env["cartes.reduction"].search([
            ("carte_type", "=", "bronze"),
            ("active", "=", True),
        ], limit=1)
        with patch.object(type(self.env["mail.mail"]), "send") as send_mail:
            notification = self.partner.with_user(
                self.pos_user
            )._send_card_lifecycle_notification(
                bronze,
                "points_awarded",
                "Points attributed",
                "Points have been added.",
            )

        self.assertEqual(notification.client_id, self.partner)
        self.assertEqual(notification.notification_type, "points_awarded")
        send_mail.assert_called_once()


class TestPass2SetupAndInvoices(TransactionCase):

    def test_signup_partner_has_tunisian_checkout_defaults(self):
        configure_pass1_data(self.env)
        partner = self.env["res.partner"].create({
            "name": "Tunis Signup Checkout",
            "email": "tunis-checkout@example.com",
        })
        self.assertEqual(partner.country_id.code, "TN")
        self.assertEqual(partner.city, "Tunis")
        self.assertTrue(partner.street)
        self.assertTrue(partner.phone)
        welcome_mail = self.env["mail.mail"].search([
            ("email_to", "=", partner.email),
            ("mail_message_id.subject", "ilike", "Bienvenue"),
        ], limit=1)
        self.assertIn("Programme fidélité", welcome_mail.body_html)
        self.assertIn("card-bronze-2.png", welcome_mail.body_html)
        attribution = self.env["cartes.attribution"].search([
            ("client_id", "=", partner.id),
            ("state", "=", "validated"),
        ], limit=1)
        self.assertRegex(attribution.reference, r"^CARD-\d{6}$")
        self.assertIn(partner.name, welcome_mail.body_html)
        self.assertIn(attribution.reference, welcome_mail.body_html)
        self.assertIn("/my/cartes", welcome_mail.body_html)

    def test_ttc_tax_is_company_default_and_applies_to_sale_products(self):
        tnd = self.env.ref("base.TND")
        company = self.env["res.company"].search([
            ("name", "=", "Sotufab Distribution Tunisie"),
            ("currency_id", "=", tnd.id),
        ], limit=1)
        self.assertTrue(company, "A TND Sotufab company is required for this test.")
        self.env = self.env(context={
            **self.env.context,
            "allowed_company_ids": company.ids,
        })
        product = self.env["product.template"].with_company(company).create({
            "name": "TTC hook coverage product",
            "company_id": company.id,
            "sale_ok": True,
            "taxes_id": [(5, 0, 0)],
        })

        self.assertTrue(configure_tunisian_ttc_taxes(self.env))

        sales_tax = self.env.company.account_sale_tax_id
        self.assertEqual(sales_tax.name, "TVA 19%")
        self.assertEqual(sales_tax.amount, 19.0)
        self.assertTrue(sales_tax.price_include)
        self.assertEqual(product.taxes_id, sales_tax)

    def test_e2e_seed_is_published_and_idempotent(self):
        tnd = self.env.ref("base.TND")
        company = self.env["res.company"].search([
            ("name", "=", "Sotufab Distribution Tunisie"),
            ("currency_id", "=", tnd.id),
        ], limit=1)
        self.assertTrue(company, "A TND Sotufab company is required for this test.")
        self.env = self.env(context={
            **self.env.context,
            "allowed_company_ids": company.ids,
        })
        first = configure_pass2_e2e(self.env)
        second = configure_pass2_e2e(self.env)
        expected = {
            "Office Chair Black": 120.0,
            "Desk Stand with Screen": 45.0,
            "Executive Desk": 450.0,
        }
        for name, price in expected.items():
            product = second["products"].filtered(lambda item: item.name == name)
            self.assertEqual(len(product), 1)
            self.assertEqual(product.list_price, price)
            self.assertTrue(product.website_published)
            self.assertEqual(product.taxes_id, second["sales_tax"])
        self.assertEqual(second["sales_tax"].name, "TVA 19%")
        self.assertEqual(second["sales_tax"].amount, 19.0)
        self.assertEqual(second["sales_tax"].tax_group_id.name, "TVA 19%")
        self.assertTrue(second["sales_tax"].price_include)
        self.assertFalse(second["domestic_fiscal_position"].vat_required)
        self.assertEqual(
            second["website"].show_line_subtotals_tax_selection,
            "tax_included",
        )
        self.assertEqual(first["payment_provider"], second["payment_provider"])
        self.assertEqual(second["payment_provider"].state, "test")
        self.assertTrue(second["payment_provider"].is_published)
        customer = self.env["res.partner"].with_context(
            skip_card_eval=True
        ).create({
            "name": "TVA 19% website customer",
            "country_id": self.env.ref("base.tn").id,
        })
        order = self.env["sale.order"].create({
            "partner_id": customer.id,
            "website_id": second["website"].id,
            "pricelist_id": second["pricelist"].id,
        })
        self.assertEqual(order.company_id, company)
        self.assertEqual(order.fiscal_position_id.company_id, company)
        self.assertEqual(
            order.fiscal_position_id.country_id,
            self.env.ref("base.tn"),
        )
        self.assertFalse(order.fiscal_position_id.vat_required)
        line = self.env["sale.order.line"].create({
            "order_id": order.id,
            "product_id": second["products"][0].product_variant_id.id,
            "product_uom_qty": 1,
            "price_unit": 120.0,
        })
        self.assertEqual(line.tax_id, second["sales_tax"])
        self.assertAlmostEqual(order.amount_total, 120.0, places=2)
        self.assertAlmostEqual(order.amount_untaxed, 100.84, places=2)
        self.assertAlmostEqual(order.amount_tax, 19.16, places=2)
        pos_config = self.env["pos.config"].search([
            ("company_id", "=", self.env.company.id),
            ("active", "=", True),
        ], limit=1)
        self.assertTrue(pos_config)
        self.assertTrue(pos_config.iface_print_auto)
        self.assertEqual(pos_config.iface_tax_included, "total")
        self.assertEqual(
            pos_config.default_fiscal_position_id,
            second["domestic_fiscal_position"],
        )
        pos_tax = pos_config.default_fiscal_position_id.map_tax(
            second["products"][0].taxes_id
        )
        pos_totals = pos_tax.compute_all(
            120.0,
            currency=second["pricelist"].currency_id,
            quantity=1,
            product=second["products"][0].product_variant_id,
            partner=customer,
        )
        self.assertAlmostEqual(pos_totals["total_included"], 120.0, places=2)
        self.assertAlmostEqual(pos_totals["total_excluded"], 100.84, places=2)
        self.assertAlmostEqual(
            sum(item["amount"] for item in pos_totals["taxes"]),
            19.16,
            places=2,
        )
        journal = self.env["account.journal"].search([
            ("type", "=", "sale"),
            ("company_id", "=", self.env.company.id),
        ], limit=1)
        income_account = journal.default_account_id or self.env[
            "account.account"
        ].search([
            ("company_id", "=", self.env.company.id),
            ("account_type", "in", ["income", "income_other"]),
        ], limit=1)
        invoice = self.env["account.move"].create({
            "move_type": "out_invoice",
            "partner_id": customer.id,
            "journal_id": journal.id,
            "invoice_line_ids": [(0, 0, {
                "name": "TTC invoice verification",
                "product_id": second["products"][0].product_variant_id.id,
                "quantity": 1,
                "price_unit": 120.0,
                "account_id": income_account.id,
                "tax_ids": [(6, 0, second["sales_tax"].ids)],
            })],
        })
        invoice.action_post()
        self.assertAlmostEqual(invoice.amount_total, 120.0, places=2)
        self.assertAlmostEqual(invoice.amount_untaxed, 100.84, places=2)
        self.assertAlmostEqual(invoice.amount_tax, 19.16, places=2)
        self.assertEqual(
            self.env["product.association"].search_count([
                ("is_generated", "=", False),
                ("associated_product_id", "in", second["products"].mapped(
                    "product_variant_id"
                ).ids),
            ]),
            3,
        )
        session = self.env["pos.session"].search([
            ("config_id", "=", pos_config.id),
        ], limit=1)
        self.assertTrue(session)
        card_fields = session._loader_params_cartes_reduction()[
            "search_params"
        ]["fields"]
        self.assertIn("carte_type", card_fields)
        self.assertIn("discount", card_fields)
        self.assertIn("active", card_fields)
        partner_fields = session._loader_params_res_partner()[
            "search_params"
        ]["fields"]
        self.assertIn("parent_id", partner_fields)
        order_values = self.env["pos.order"]._order_fields({
            "user_id": self.env.uid,
            "pos_session_id": session.id,
            "lines": [],
            "name": "Pass 2 automatic invoice test",
            "sequence_number": 1,
            "partner_id": False,
            "date_order": fields.Datetime.to_string(fields.Datetime.now()),
            "fiscal_position_id": False,
            "pricelist_id": pos_config.pricelist_id.id,
            "amount_paid": 10.0,
            "amount_total": 10.0,
            "amount_tax": 0.0,
            "amount_return": 0.0,
            "to_invoice": False,
        })
        self.assertTrue(order_values["to_invoice"])

    def test_posted_invoice_email_attaches_the_official_pdf_once(self):
        partner = self.env["res.partner"].with_context(
            skip_card_eval=True
        ).create({
            "name": "Pass 2 invoice recipient",
            "email": "pass2-invoice@example.com",
        })
        invoice_contact = self.env["res.partner"].with_context(
            skip_card_eval=True
        ).create({
            "name": "Pass 2 billing contact",
            "email": "stale-billing@example.com",
            "parent_id": partner.id,
            "type": "invoice",
        })
        website = self.env["website"].search([], limit=1)
        order = self.env["sale.order"].with_context(
            skip_card_eval=True
        ).create({
            "partner_id": partner.id,
            "website_id": website.id,
        })
        journal = self.env["account.journal"].search([
            ("type", "=", "sale"),
            ("company_id", "=", self.env.company.id),
        ], limit=1)
        self.assertTrue(journal, "The TND E2E database needs a sales journal.")
        income_account = journal.default_account_id or self.env[
            "account.account"
        ].search([
            ("company_id", "=", self.env.company.id),
            ("account_type", "in", ["income", "income_other"]),
        ], limit=1)
        invoice = self.env["account.move"].create({
            "move_type": "out_invoice",
            "partner_id": invoice_contact.id,
            "journal_id": journal.id,
            "invoice_origin": order.name,
            "invoice_line_ids": [(0, 0, {
                "name": "Pass 2 invoice test line",
                "quantity": 1,
                "price_unit": 10,
                "account_id": income_account.id,
            })],
        })
        invoice.action_post()
        groups = self.env.ref("base.group_user") | self.env.ref(
            "point_of_sale.group_pos_user"
        )
        cashier = self.env["res.users"].with_context(
            skip_card_eval=True
        ).create({
            "name": "Pass 2 invoice cashier",
            "login": "pass2-invoice-cashier",
            "groups_id": [(6, 0, groups.ids)],
        })
        report_model = type(self.env["ir.actions.report"])
        with (
            patch.object(
                report_model,
                "_render_qweb_pdf",
                return_value=(b"%PDF-test", "pdf"),
            ) as render_pdf,
            patch.object(type(self.env["mail.mail"]), "send") as send_mail,
        ):
            invoice.with_user(cashier)._send_cartes_invoice_email()
            invoice.with_user(cashier)._send_cartes_invoice_email()

        self.assertTrue(invoice.cartes_invoice_email_sent)
        self.assertEqual(render_pdf.call_count, 1)
        self.assertEqual(send_mail.call_count, 1)
        attachment = self.env["ir.attachment"].search([
            ("res_model", "=", "account.move"),
            ("res_id", "=", invoice.id),
            ("mimetype", "=", "application/pdf"),
        ], limit=1)
        self.assertTrue(attachment)
        sent_mail = self.env["mail.mail"].search([
            ("email_to", "=", partner.email),
            ("mail_message_id.subject", "=", f"Votre facture {invoice.name}"),
        ], limit=1)
        self.assertTrue(sent_mail)
        self.assertIn("TVA applicable", sent_mail.body_html)
        self.assertNotIn("/static/img/cards/", sent_mail.body_html)
        self.assertEqual(len(sent_mail.attachment_ids), 1)
        self.assertEqual(sent_mail.attachment_ids.mimetype, "application/pdf")

    def test_pos_invoice_email_uses_selected_customer_address(self):
        partner = self.env["res.partner"].with_context(
            skip_card_eval=True
        ).create({
            "name": "Pass 2 POS invoice customer",
            "email": "pass2-pos-invoice@example.com",
        })
        invoice_contact = self.env["res.partner"].with_context(
            skip_card_eval=True
        ).create({
            "name": "Pass 2 stale POS billing contact",
            "email": "stale-pos-billing@example.com",
            "parent_id": partner.id,
            "type": "invoice",
        })
        session = self.env["pos.session"].search([
            ("company_id", "=", self.env.company.id),
        ], limit=1)
        self.assertTrue(session, "The TND E2E database needs an open POS session.")
        journal = self.env["account.journal"].search([
            ("type", "=", "sale"),
            ("company_id", "=", self.env.company.id),
        ], limit=1)
        income_account = journal.default_account_id or self.env[
            "account.account"
        ].search([
            ("company_id", "=", self.env.company.id),
            ("account_type", "in", ["income", "income_other"]),
        ], limit=1)
        invoice = self.env["account.move"].create({
            "move_type": "out_invoice",
            "partner_id": invoice_contact.id,
            "journal_id": journal.id,
            "invoice_line_ids": [(0, 0, {
                "name": "Pass 2 POS invoice line",
                "quantity": 1,
                "price_unit": 10,
                "account_id": income_account.id,
            })],
        })
        invoice.action_post()
        self.env["pos.order"].create({
            "name": "Pass 2 POS invoice order",
            "session_id": session.id,
            "company_id": self.env.company.id,
            "partner_id": partner.id,
            "account_move": invoice.id,
            "state": "invoiced",
            "amount_paid": 10,
            "amount_total": 10,
            "amount_tax": 0,
            "amount_return": 0,
        })
        groups = self.env.ref("base.group_user") | self.env.ref(
            "point_of_sale.group_pos_user"
        )
        cashier = self.env["res.users"].with_context(
            skip_card_eval=True
        ).create({
            "name": "Pass 2 POS invoice mail cashier",
            "login": "pass2-pos-invoice-mail-cashier",
            "groups_id": [(6, 0, groups.ids)],
        })

        with (
            patch.object(
                type(self.env["ir.actions.report"]),
                "_render_qweb_pdf",
                return_value=(b"%PDF-test", "pdf"),
            ),
            patch.object(type(self.env["mail.mail"]), "send"),
        ):
            invoice.with_user(cashier)._send_cartes_invoice_email()

        sent_mail = self.env["mail.mail"].search([
            ("email_to", "=", partner.email),
            ("mail_message_id.subject", "=", f"Votre facture {invoice.name}"),
        ], limit=1)
        self.assertTrue(sent_mail)
        self.assertFalse(self.env["mail.mail"].search_count([
            ("email_to", "=", invoice_contact.email),
            ("mail_message_id.subject", "=", f"Votre facture {invoice.name}"),
        ]))
