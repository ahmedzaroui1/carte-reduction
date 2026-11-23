"""Rollback-only security checks for the Cartes de Réduction addon.

Run with: odoo shell -d odoo17 < scripts/verify_security_100.py
Business fixtures are rolled back; immutable security audit events remain committed.
"""
import hashlib
import hmac
import secrets
import sys
from datetime import date, timedelta

from odoo.exceptions import UserError


results = []
fixture = {}


def run_check(name, callback):
    try:
        with env.cr.savepoint():
            callback()
        results.append(True)
        print("PASS: %s" % name)
    except Exception as exc:
        results.append(False)
        print("FAIL: %s (%s: %s)" % (name, type(exc).__name__, exc))


def check_issuance():
    partner = env['res.partner'].create({'name': 'Security Verification Customer'})
    product = env['product.product'].create({
        'name': 'Security Verification Product',
        'type': 'consu',
        'list_price': 100.0,
        'taxes_id': [(6, 0, [])],
    })
    card = env['cartes.reduction'].create({
        'name': 'Security Verification Card',
        'discount': 10.0,
        'active': True,
        'state': 'active',
    })
    attribution = env['cartes.attribution'].create({
        'client_id': partner.id,
        'carte_ids': [(6, 0, [card.id])],
        'state': 'validated',
        'date_expiration': date.today() + timedelta(days=30),
    })
    token = attribution.signed_token
    payload, signature = token.rsplit('.', 1)
    expected = hmac.new(
        attribution._security_hmac_secret(), payload.encode('utf-8'), hashlib.sha256
    ).hexdigest()
    assert payload.startswith('v1:%s:%s:' % (attribution.id, partner.id))
    assert len(signature) == 64 and hmac.compare_digest(signature, expected)
    fixture.update(
        partner=partner, product=product, card=card,
        attribution=attribution, token=token,
    )


def check_valid_redemption():
    attribution = fixture['attribution']
    old_nonce = attribution.nonce
    order = env['sale.order'].create({
        'partner_id': fixture['partner'].id,
        'security_card_token': fixture['token'],
    })
    line = env['sale.order.line'].create({
        'order_id': order.id,
        'product_id': fixture['product'].id,
        'name': fixture['product'].name,
        'product_uom_qty': 1,
        'price_unit': 100.0,
    })
    order.action_apply_secure_token()
    assert attribution.nonce != old_nonce
    assert attribution.signed_token != fixture['token']
    assert not order.security_card_token
    assert order.applied_discount_card_id == fixture['card']
    assert round(line.discount, 2) == 10.00
    assert round(order.amount_untaxed, 2) == 90.00
    assert env['cartes_reduction.security.audit'].search_count([
        ('event_type', '=', 'valid_redeem'),
        ('card_ref', '=', str(attribution.id)),
    ]) >= 1


def check_tampered_token():
    tampered = fixture['token'].replace(':10.0000:', ':99.0000:', 1)
    assert tampered != fixture['token']
    try:
        env['cartes.attribution'].verify_and_redeem_token(tampered, channel='api')
    except UserError:
        pass
    else:
        raise AssertionError("Tampered token was accepted.")
    assert env['cartes_reduction.security.audit'].search_count([
        ('event_type', '=', 'tampered_token'),
        ('severity', '=', 'critical'),
        ('card_ref', '=', str(fixture['attribution'].id)),
    ]) >= 1


def check_replay():
    try:
        env['cartes.attribution'].verify_and_redeem_token(fixture['token'], channel='api')
    except UserError:
        pass
    else:
        raise AssertionError("Previously redeemed token was accepted.")
    assert env['cartes_reduction.security.audit'].search_count([
        ('event_type', '=', 'replay_blocked'),
        ('severity', '=', 'critical'),
        ('card_ref', '=', str(fixture['attribution'].id)),
    ]) >= 1


def check_immutability():
    audit = env['cartes_reduction.security.audit'].search([], limit=1)
    if not audit:
        raise AssertionError("No audit event is available for immutability checks.")
    try:
        audit.write({'details': 'tamper attempt'})
    except UserError:
        pass
    else:
        raise AssertionError("Audit write was not blocked.")
    try:
        audit.unlink()
    except UserError:
        pass
    else:
        raise AssertionError("Audit unlink was not blocked.")


def check_cin_encryption_and_masking():
    cin_value = "98%s" % secrets.token_hex(5).upper()
    client = env['cartes.client'].create({
        'name': 'Security Verification',
        'prenom': 'Temporary',
        'num_telephone': '00000000',
        'cin': cin_value,
    })
    env.cr.execute(
        "SELECT cin_encrypted FROM cartes_client WHERE id = %s", [client.id]
    )
    ciphertext = env.cr.fetchone()[0]
    assert ciphertext and ciphertext != cin_value and cin_value not in ciphertext
    env.cr.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'cartes_client' AND column_name = 'cin'"
    )
    assert not env.cr.fetchone(), "Plaintext CIN database column remains."
    assert client.cin == cin_value

    ordinary_user = env['res.users'].create({
        'name': 'Temporary Security Verification User',
        'login': 'security-verify-%s' % secrets.token_hex(6),
        'groups_id': [(6, 0, [env.ref('base.group_user').id])],
    })
    masked = client.with_user(ordinary_user).cin_masked
    assert masked == '****' + cin_value[-4:]


run_check("Card issuance creates a valid v1 HMAC-SHA256 token", check_issuance)
run_check("Valid redemption rotates nonce and persists valid_redeem audit event", check_valid_redemption)
run_check("Tampered token is rejected and persists critical tampered_token event", check_tampered_token)
run_check("Spent token replay is rejected and persists critical replay_blocked event", check_replay)
run_check("Audit write and unlink are rejected", check_immutability)
run_check("CIN is encrypted at rest and masked for ordinary users", check_cin_encryption_and_masking)

env.cr.rollback()
passed = sum(results)
print("Security verification: %s/6 passed, %s failed" % (passed, len(results) - passed))
if not all(results):
    sys.exit(1)
