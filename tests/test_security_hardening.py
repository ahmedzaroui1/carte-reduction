import os
import hashlib
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.modules.registry import Registry
from odoo.tests import TransactionCase

from odoo.addons.cartes_reduction.models.security_utils import (
    find_injection_signature,
    get_fernet,
    get_hmac_secret,
    reject_suspicious_input,
    verify_pin,
)
from odoo.addons.cartes_reduction.controllers.main import LoyaltyController


class TestSecurityHardening(TransactionCase):

    def setUp(self):
        super().setUp()
        self.partner = self.env["res.partner"].with_context(
            skip_card_eval=True
        ).create({
            "name": "Security test customer",
            "email": "security-test@example.com",
        })
        groups = self.env.ref("base.group_user") | self.env.ref(
            "point_of_sale.group_pos_user"
        )
        self.pos_user = self.env["res.users"].with_context(
            skip_card_eval=True
        ).create({
            "name": "Security test cashier",
            "login": "security-test-cashier",
            "email": "security-test-cashier@example.com",
            "groups_id": [(6, 0, groups.ids)],
        })

    def test_cin_is_encrypted_masked_and_unique(self):
        client = self.env["cartes.client"].create({
            "name": "CIN",
            "prenom": "Test",
            "num_telephone": "00000000",
            "cin": "12345678",
        })

        self.assertTrue(client.cin_encrypted.startswith("gAAAAAB"))
        self.assertNotIn("12345678", client.cin_encrypted)
        self.assertEqual(client.cin_masked, "****5678")
        self.assertNotEqual(client.cin_hash, "12345678")
        self.assertFalse(client.cin)

        with self.assertRaises(ValidationError):
            self.env["cartes.client"].create({
                "name": "Duplicate CIN",
                "prenom": "Test",
                "num_telephone": "00000001",
                "cin": "12345678",
            })

        self.env.cr.execute(
            """
            SELECT 1
              FROM information_schema.columns
             WHERE table_schema = current_schema()
               AND table_name = 'cartes_client'
               AND column_name = 'cin'
            """
        )
        self.assertFalse(self.env.cr.fetchone())

    def test_cin_reveal_is_admin_only_and_audited(self):
        client = self.env["cartes.client"].create({
            "name": "CIN Reveal",
            "prenom": "Test",
            "num_telephone": "00000002",
            "cin": "87654321",
        })

        result = client.action_reveal_cin()
        self.assertEqual(result["params"]["message"], "87654321")
        event = self.env["cartes.reduction.security.audit"].search([
            ("event_type", "=", "cin_reveal"),
            ("client_record_id", "=", client.id),
        ], limit=1)
        self.assertTrue(event)
        self.assertNotIn("87654321", event.details or "")

        with self.assertRaises(AccessError):
            client.with_user(self.pos_user).action_reveal_cin()

        with self.assertRaises(AccessError):
            event.write({"details": "tampered"})
        with self.assertRaises(AccessError):
            event.unlink()

    def test_partner_cin_and_pin_helpers_enforce_masking_and_lockout(self):
        self.partner.write({"cin": "11223344"})
        self.assertEqual(self.partner.cin_masked, "****3344")
        self.assertNotIn("11223344", self.partner.cin_encrypted)

        self.assertTrue(self.partner.set_personal_security_pin("1234"))
        self.assertNotEqual(self.partner.pin_hash, "1234")
        self.assertTrue(verify_pin(self.partner.pin_hash, "1234"))
        self.assertFalse(verify_pin(self.partner.pin_hash, "9999"))
        with self.assertRaises(ValidationError):
            self.partner.set_personal_security_pin("123")

        partner_as_cashier = self.partner.with_user(self.pos_user)
        self.assertTrue(partner_as_cashier.verify_personal_security_pin("1234"))
        for _attempt in range(3):
            result = partner_as_cashier.verify_personal_security_pin("0000")
            self.assertFalse(result)
        self.assertEqual(self.partner.pos_otp_attempts, 3)
        self.assertTrue(self.partner.pos_otp_locked_until)

    def test_environment_keys_fail_closed_outside_test_mode(self):
        module = "odoo.addons.cartes_reduction.models.security_utils"
        with (
            patch(f"{module}._test_mode_enabled", return_value=False),
            patch.dict(os.environ, {"CARTES_HMAC_SECRET": ""}),
        ):
            with self.assertRaises(RuntimeError):
                get_hmac_secret()
        with (
            patch(f"{module}._test_mode_enabled", return_value=False),
            patch.dict(os.environ, {"CARTES_FERNET_KEY": "invalid"}),
        ):
            with self.assertRaises(RuntimeError):
                get_fernet()

    def test_card_redemption_velocity_records_abuse(self):
        card = self.env["cartes.reduction"].search([
            ("carte_type", "=", "bronze"),
            ("active", "=", True),
        ], limit=1)
        self.assertTrue(card)
        partner = self.partner.with_context(enforce_security_checks=True)
        first_order = SimpleNamespace(_name="sale.order", name="SECURITY-1")
        second_order = SimpleNamespace(_name="sale.order", name="SECURITY-2")

        partner._record_card_redemption(first_order, card)
        with self.assertRaisesRegex(UserError, "VELOCITY_ABUSE"):
            partner._record_card_redemption(second_order, card)

        event = self.env["cartes.reduction.security.audit"].search([
            ("event_type", "=", "velocity_abuse"),
            ("partner_id", "=", self.partner.id),
        ], limit=1)
        self.assertTrue(event)

    def test_security_row_lock_rejects_concurrent_redemption(self):
        registry = Registry(self.env.cr.dbname)
        with registry.cursor() as locking_cursor:
            locking_cursor.execute(
                """
                SELECT id
                  FROM res_partner
                 ORDER BY id
                 LIMIT 1
                 FOR UPDATE SKIP LOCKED
                """
            )
            row = locking_cursor.fetchone()
            self.assertTrue(row, "Expected to find an unlocked partner row.")
            locked_partner_id = row[0]
            partner = self.env["res.partner"].browse(locked_partner_id)
            self.assertFalse(partner._try_lock_security_row())

        event = self.env["cartes.reduction.security.audit"].search([
            ("event_type", "=", "double_spend_blocked"),
            ("partner_id", "=", locked_partner_id),
            ("details", "=", "security row is locked by a concurrent operation"),
        ], limit=1)
        self.assertTrue(event)

    def test_audit_chain_verification_and_sql_payload_modification(self):
        from odoo.addons.cartes_reduction.models.security_utils import log_security_event

        log_security_event(
            self.env,
            "cin_reveal",
            partner_id=self.partner.id,
            details="chain verification test",
        )
        result = self.env["cartes.reduction.security.audit"].verify_audit_chain()
        self.assertTrue(result["valid"])

        record = self.env["cartes.reduction.security.audit"].search([], limit=1, order="id desc")
        self.env.cr.execute(
            "UPDATE cartes_reduction_security_audit SET details = %s WHERE id = %s",
            ("DIRECT_SQL_MODIFIED", record.id),
        )
        self.env.invalidate_all()
        tampered = self.env["cartes.reduction.security.audit"].verify_audit_chain()
        self.assertFalse(tampered["valid"])
        self.assertIn(tampered["reason"], ("payload_or_hash_modified", "broken_link"))

    def test_ml_artifact_checksum_is_fail_closed(self):
        partner = self.partner
        with tempfile.NamedTemporaryFile(delete=False) as artifact:
            artifact.write(b"tampered-model-artifact")
            model_path = artifact.name
        manifest_path = f"{model_path}.sha256"
        try:
            wrong_checksum = "0" * 64
            with (
                patch.dict(os.environ, {"CARTES_ML_MODEL_SHA256": wrong_checksum}),
                patch("odoo.addons.cartes_reduction.models.res_partner.joblib.load") as load,
                patch("odoo.addons.cartes_reduction.models.res_partner.os.path.exists", return_value=True),
                patch("odoo.addons.cartes_reduction.models.res_partner.os.path.join", return_value=model_path),
            ):
                self.assertFalse(partner._get_ml_model())
                load.assert_not_called()
            event = self.env["cartes.reduction.security.audit"].search([
                ("event_type", "=", "ml_integrity_failure"),
                ("partner_id", "=", partner.id),
            ], limit=1)
            self.assertTrue(event)

            correct_checksum = hashlib.sha256(b"tampered-model-artifact").hexdigest()
            sentinel = object()
            with (
                patch.dict(os.environ, {"CARTES_ML_MODEL_SHA256": correct_checksum}),
                patch("odoo.addons.cartes_reduction.models.res_partner.joblib.load", return_value=sentinel),
                patch("odoo.addons.cartes_reduction.models.res_partner.os.path.exists", return_value=True),
                patch("odoo.addons.cartes_reduction.models.res_partner.os.path.join", return_value=model_path),
            ):
                self.assertIs(partner._get_ml_model(), sentinel)
            with open(manifest_path, "r", encoding="ascii") as manifest:
                self.assertEqual(manifest.read().strip(), correct_checksum)

            with open(manifest_path, "w", encoding="ascii") as manifest:
                manifest.write(wrong_checksum)
            with (
                patch.dict(os.environ, {"CARTES_ML_MODEL_SHA256": correct_checksum}),
                patch("odoo.addons.cartes_reduction.models.res_partner.joblib.load") as load,
                patch("odoo.addons.cartes_reduction.models.res_partner.os.path.exists", return_value=True),
                patch("odoo.addons.cartes_reduction.models.res_partner.os.path.join", return_value=model_path),
            ):
                self.assertFalse(partner._get_ml_model())
                load.assert_not_called()
        finally:
            for path in (model_path, manifest_path):
                if os.path.exists(path):
                    os.unlink(path)

    def test_injection_patterns_are_rejected_without_storing_input(self):
        self.assertEqual(find_injection_signature("%3Cscript%3E"), "xss_script")
        self.assertEqual(find_injection_signature("UNION SELECT"), "sqli_union")
        self.assertEqual(find_injection_signature("' OR 1=1"), "sqli_boolean")
        self.assertFalse(find_injection_signature("ordinary product search"))

        rejected = reject_suspicious_input(
            self.env,
            "/shop/check_product_suggestions",
            {"q": "<script>alert('private')</script>"},
        )
        self.assertTrue(rejected)
        event = self.env["cartes.reduction.security.audit"].search([
            ("event_type", "=", "injection_blocked"),
        ], limit=1, order="id desc")
        self.assertTrue(event)
        self.assertIn("signature=xss_script", event.details)
        self.assertNotIn("private", event.details)

    def test_cross_client_card_data_request_is_audited(self):
        portal_user = self.env["res.users"].with_context(
            skip_card_eval=True
        ).create({
            "name": "Security test portal user",
            "login": "security-test-portal",
            "email": "security-test-portal@example.com",
            "groups_id": [(6, 0, self.env.ref("base.group_portal").ids)],
        })
        request_env = self.env["res.partner"].with_user(portal_user).env

        with patch(
            "odoo.addons.cartes_reduction.controllers.main.request",
            SimpleNamespace(env=request_env),
        ):
            result = LoyaltyController().get_cartes_data(self.partner.id)

        self.assertEqual(result, {"success": False, "error": "Unauthorized"})
        event = self.env["cartes.reduction.security.audit"].search([
            ("event_type", "=", "idor_blocked"),
            ("partner_id", "=", self.partner.id),
            ("user_id", "=", portal_user.id),
            ("details", "=", "route=/sale/order/get_cartes_data; scope=partner"),
        ], limit=1)
        self.assertTrue(event)

    def test_external_audit_seal_is_chained_and_detects_modification(self):
        from odoo.addons.cartes_reduction.models import security_audit

        with tempfile.TemporaryDirectory() as directory:
            seal_path = os.path.join(directory, "head_hash.seal")
            with (
                patch.object(security_audit, "_SEAL_PATH", seal_path),
                patch.object(
                    security_audit,
                    "get_audit_seal_hmac_secret",
                    return_value=b"x" * 32,
                ),
            ):
                first_head = "a" * 64
                second_head = "b" * 64
                self.assertEqual(security_audit._initialize_external_seal(first_head), 1)
                self.assertEqual(
                    security_audit._append_external_seal(
                        second_head, {first_head, second_head}
                    ),
                    2,
                )
                verified = security_audit._read_seal_chain()
                self.assertTrue(verified["valid"])
                self.assertEqual(verified["sequence"], 2)
                self.assertEqual(verified["head_hash"], second_head)

                with open(seal_path, "r+", encoding="utf-8") as seal_file:
                    lines = seal_file.readlines()
                    modified = lines[-1].replace(second_head, "c" * 64)
                    lines[-1] = modified
                    seal_file.seek(0)
                    seal_file.writelines(lines)
                    seal_file.truncate()
                self.assertFalse(security_audit._read_seal_chain()["valid"])
