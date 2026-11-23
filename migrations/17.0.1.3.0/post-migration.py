from odoo import SUPERUSER_ID, api

from odoo.addons.cartes_reduction.hooks import configure_tunisian_ttc_taxes
from odoo.addons.cartes_reduction.models.security_utils import (
    cin_blind_index,
    encrypt_cin,
    normalize_cin,
    validate_security_secrets,
)


def _column_exists(cr, table, column):
    cr.execute(
        """
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = current_schema()
           AND table_name = %s
           AND column_name = %s
        """,
        (table, column),
    )
    return bool(cr.fetchone())


def _migrate_plaintext_cin(cr, table):
    if not _column_exists(cr, table, "cin"):
        return
    for column in ("cin_encrypted", "cin_hash"):
        if not _column_exists(cr, table, column):
            raise RuntimeError(
                f"Encrypted CIN storage is unavailable on table {table}."
            )

    cr.execute(
        f'SELECT id, cin, cin_encrypted, cin_hash FROM "{table}" '
        "ORDER BY id"
    )
    rows = cr.fetchall()
    existing_hashes = set()
    seen_hashes = set()
    for record_id, raw_cin, ciphertext, stored_hash in rows:
        if stored_hash:
            existing_hashes.add(stored_hash)
    duplicates = 0
    for record_id, raw_cin, ciphertext, stored_hash in rows:
        if not raw_cin or not normalize_cin(raw_cin):
            continue
        digest = cin_blind_index(raw_cin)
        if digest in existing_hashes or digest in seen_hashes:
            duplicates += 1
        seen_hashes.add(digest)
    if duplicates:
        raise RuntimeError(
            f"Cannot encrypt CIN: {duplicates} duplicate blind indexes exist in {table}."
        )

    for record_id, raw_cin, ciphertext, stored_hash in rows:
        if not raw_cin or not normalize_cin(raw_cin):
            continue
        if ciphertext and stored_hash:
            raise RuntimeError(
                f"Both legacy and encrypted CIN values exist in {table}; manual review required."
            )
        cr.execute(
            f'UPDATE "{table}" SET cin_encrypted = %s, cin_hash = %s WHERE id = %s',
            (encrypt_cin(raw_cin), cin_blind_index(raw_cin), record_id),
        )

    cr.execute(f'ALTER TABLE "{table}" DROP COLUMN cin')


def migrate(cr, version):
    validate_security_secrets()
    _migrate_plaintext_cin(cr, "cartes_client")
    _migrate_plaintext_cin(cr, "res_partner")

    cr.execute(
        "DELETE FROM ir_config_parameter "
        "WHERE key = 'cartes_reduction.pos_otp_secret'"
    )
    cr.execute(
        """
        UPDATE res_partner
           SET pos_otp_digest = NULL,
               pos_otp_qr_digest = NULL,
               pos_otp_nonce_digest = NULL,
               pos_otp_expires_at = NULL,
               pos_otp_order_uid = NULL,
               pos_otp_card_id = NULL,
               pos_verified_order_uid = NULL,
               pos_verified_card_id = NULL,
               pos_verified_until = NULL
         WHERE pos_otp_digest IS NOT NULL
            OR pos_otp_qr_digest IS NOT NULL
            OR pos_otp_nonce_digest IS NOT NULL
            OR pos_otp_expires_at IS NOT NULL
            OR pos_otp_order_uid IS NOT NULL
            OR pos_otp_card_id IS NOT NULL
            OR pos_verified_order_uid IS NOT NULL
            OR pos_verified_card_id IS NOT NULL
            OR pos_verified_until IS NOT NULL
        """
    )

    env = api.Environment(cr, SUPERUSER_ID, {})
    configure_tunisian_ttc_taxes(env)
