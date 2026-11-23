import base64
import binascii
import hashlib
import hmac
import os
import re
import secrets
import unicodedata

from cryptography.fernet import Fernet, InvalidToken

from odoo import SUPERUSER_ID, api
from odoo.modules.registry import Registry
from odoo.tools import config


_TEST_HMAC_SECRET = hashlib.sha256(
    b"cartes-reduction-test-hmac-secret-v1"
).digest()
_TEST_FERNET_KEY = base64.urlsafe_b64encode(
    hashlib.sha256(b"cartes-reduction-test-fernet-key-v1").digest()
)
_PBKDF2_ITERATIONS = 600_000
_PIN_HASH_PREFIX = "pbkdf2_sha256"


def _test_mode_enabled():
    return bool(config["test_enable"])


def get_hmac_secret():
    secret = os.environ.get("CARTES_HMAC_SECRET")
    if not secret:
        if _test_mode_enabled():
            return _TEST_HMAC_SECRET
        raise RuntimeError("CARTES_HMAC_SECRET is required for security operations.")
    secret_bytes = secret.encode("utf-8")
    if len(secret_bytes) < 32:
        raise RuntimeError("CARTES_HMAC_SECRET must contain at least 32 bytes.")
    return secret_bytes


def get_fernet():
    key = os.environ.get("CARTES_FERNET_KEY")
    if not key:
        if _test_mode_enabled():
            key = _TEST_FERNET_KEY.decode("ascii")
        else:
            raise RuntimeError("CARTES_FERNET_KEY is required for CIN encryption.")
    try:
        return Fernet(key.encode("ascii"))
    except (binascii.Error, UnicodeEncodeError, ValueError, TypeError) as error:
        raise RuntimeError("CARTES_FERNET_KEY is invalid.") from error


def validate_security_secrets():
    get_hmac_secret()
    get_fernet()


def normalize_cin(value):
    return unicodedata.normalize("NFKC", value).strip()


def encrypt_cin(value):
    return get_fernet().encrypt(normalize_cin(value).encode("utf-8")).decode("ascii")


def decrypt_cin(ciphertext):
    if not ciphertext:
        return False
    try:
        return get_fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeEncodeError, ValueError) as error:
        raise RuntimeError("Stored CIN ciphertext is invalid.") from error


def cin_blind_index(value):
    normalized = normalize_cin(value)
    return hmac.new(
        get_hmac_secret(),
        b"cartes.client.cin:" + normalized.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def mask_cin(value):
    if not value:
        return False
    return f"****{value[-4:]}" if len(value) > 4 else "****"


def hash_pin(pin):
    if not isinstance(pin, str) or not re.fullmatch(r"[0-9]{4}", pin):
        raise ValueError("The personal security PIN must contain exactly four digits.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", pin.encode("ascii"), salt, _PBKDF2_ITERATIONS
    )
    salt_text = base64.urlsafe_b64encode(salt).decode("ascii")
    digest_text = base64.urlsafe_b64encode(digest).decode("ascii")
    return f"{_PIN_HASH_PREFIX}${_PBKDF2_ITERATIONS}${salt_text}${digest_text}"


def verify_pin(pin_hash, pin):
    if not pin_hash or not isinstance(pin, str) or not re.fullmatch(r"[0-9]{4}", pin):
        return False
    try:
        prefix, iterations_text, salt_text, expected_text = pin_hash.split("$", 3)
        iterations = int(iterations_text)
        if prefix != _PIN_HASH_PREFIX or not 100_000 <= iterations <= 1_000_000:
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(expected_text.encode("ascii"))
    except (binascii.Error, ValueError, UnicodeEncodeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", pin.encode("ascii"), salt, iterations)
    return hmac.compare_digest(expected, actual)


def log_security_event(env, event_type, partner_id=None, client_record_id=None, details=""):
    values = {
        "event_type": event_type,
        "partner_id": partner_id or False,
        "client_record_id": client_record_id or False,
        "user_id": env.uid,
        "details": details,
    }
    if _test_mode_enabled():
        env["cartes.reduction.security.audit"].sudo().create(values)
        return

    registry = Registry(env.cr.dbname)
    with registry.cursor() as cursor:
        audit_env = api.Environment(cursor, SUPERUSER_ID, {})
        audit_env["cartes.reduction.security.audit"].create(values)
        cursor.commit()
