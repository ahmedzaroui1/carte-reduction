from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase

from odoo.addons.cartes_reduction.hooks import configure_pass2_e2e


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
        self.assertEqual(send_mail.call_count, 1)

    def test_pos_otp_limits_failed_attempts_and_requires_pos_group(self):
        with (
            patch(
                "odoo.addons.cartes_reduction.models.res_partner.secrets.randbelow",
                return_value=123456,
            ),
            patch.object(type(self.env["mail.mail"]), "send"),
        ):
            self.partner._issue_pos_otp()

        for _attempt in range(5):
            result = self.partner.with_user(self.pos_user).verify_pos_otp_or_qr("000000")
        self.assertFalse(result["success"])
        self.assertEqual(self.partner.pos_otp_attempts, 5)
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
        partner = self.env["res.partner"].create({
            "name": "Tunis Signup Checkout",
            "email": "tunis-checkout@example.com",
        })
        self.assertEqual(partner.country_id.code, "TN")
        self.assertEqual(partner.city, "Tunis")
        self.assertTrue(partner.street)
        self.assertTrue(partner.phone)

    def test_e2e_seed_is_published_and_idempotent(self):
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
        self.assertEqual(first["payment_provider"], second["payment_provider"])
        self.assertEqual(second["payment_provider"].state, "test")
        self.assertTrue(second["payment_provider"].is_published)
        pos_config = self.env["pos.config"].search([
            ("company_id", "=", self.env.company.id),
            ("active", "=", True),
        ], limit=1)
        self.assertTrue(pos_config)
        self.assertTrue(pos_config.iface_print_auto)
        self.assertEqual(
            self.env["product.association"].search_count([
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
