from unittest.mock import patch

from odoo.tests import TransactionCase


class TestPinlessPosVerification(TransactionCase):

    def setUp(self):
        super().setUp()
        self.partner = self.env["res.partner"].with_context(
            skip_card_eval=True
        ).create({
            "name": "Pinless POS test customer",
            "email": "pinless-pos@example.com",
        })
        groups = self.env.ref("base.group_user") | self.env.ref(
            "point_of_sale.group_pos_user"
        )
        self.pos_user = self.env["res.users"].with_context(
            skip_card_eval=True
        ).create({
            "name": "Pinless POS test cashier",
            "login": "pinless-pos-cashier",
            "email": "pinless-pos-cashier@example.com",
            "groups_id": [(6, 0, groups.ids)],
        })

    def test_existing_customer_pin_does_not_block_otp_verification(self):
        self.partner.sudo().with_context(skip_card_eval=True).write({
            "pin_hash": "legacy-pbkdf2-hash",
        })
        with (
            patch(
                "odoo.addons.cartes_reduction.models.res_partner.secrets.randbelow",
                return_value=123456,
            ),
            patch.object(type(self.env["mail.mail"]), "send"),
        ):
            challenge = self.partner._issue_pos_otp()

        result = self.partner.with_user(
            self.pos_user
        ).verify_pos_otp_or_qr(challenge["code"])

        self.assertEqual(
            result,
            {"success": True, "message": "Client vérifié."},
        )
