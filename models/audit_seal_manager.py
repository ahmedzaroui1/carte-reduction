import fcntl
import hashlib
import hmac
import json
import logging
import os
import threading
from datetime import datetime, timezone

from odoo import SUPERUSER_ID, api, models
from odoo.exceptions import AccessError, UserError
from odoo.modules.registry import Registry
from odoo.tools import config


_logger = logging.getLogger(__name__)
_SEAL_PATH = "/var/log/odoo-audit/head_hash.seal"
_SEAL_LOCK = threading.Lock()
_SEAL_FAILURES_TOTAL = 0
_SEAL_FIELDS = ("sequence", "timestamp", "head_hash", "prev_seal_hmac")
_GENESIS_HASH = "CARTES_REDUCTION_SECURITY_AUDIT_GENESIS_V1"


def _seal_secret():
    path = "/run/secrets/audit_seal_hmac_key"
    try:
        with open(path, "rb") as secret_file:
            secret = secret_file.read().strip()
    except OSError as error:
        raise RuntimeError("The external audit seal key is unavailable.") from error
    if len(secret) < 32:
        raise RuntimeError("The external audit seal key must contain at least 32 bytes.")
    return secret


def _canonical_seal_payload(seal):
    return json.dumps(
        {field: seal[field] for field in _SEAL_FIELDS},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _read_seal_chain():
    if os.path.islink(_SEAL_PATH) or not os.path.isfile(_SEAL_PATH):
        return {"valid": False, "reason": "missing", "sequence": 0}
    try:
        secret = _seal_secret()
        descriptor = os.open(_SEAL_PATH, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "r", encoding="utf-8") as seal_file:
            lines = seal_file.readlines()
    except (OSError, RuntimeError) as error:
        return {"valid": False, "reason": "unavailable", "error": str(error), "sequence": 0}

    previous_hmac = ""
    previous_head = False
    expected_sequence = 1
    for line_number, line in enumerate(lines, start=1):
        try:
            seal = json.loads(line)
            if not isinstance(seal, dict) or set(seal) != set(_SEAL_FIELDS) | {"hmac_sha256"}:
                raise ValueError("unexpected seal fields")
            if type(seal["sequence"]) is not int or seal["sequence"] != expected_sequence:
                raise ValueError("non-monotone seal sequence")
            if seal["prev_seal_hmac"] != previous_hmac:
                raise ValueError("broken seal HMAC link")
            if not isinstance(seal["head_hash"], str) or not seal["head_hash"]:
                raise ValueError("invalid sealed head hash")
            if not isinstance(seal["timestamp"], str):
                raise ValueError("invalid seal timestamp")
            signature = hmac.new(secret, _canonical_seal_payload(seal), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(seal["hmac_sha256"], signature):
                raise ValueError("seal signature mismatch")
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            return {
                "valid": False,
                "reason": "invalid_seal",
                "line": line_number,
                "error": str(error),
                "sequence": expected_sequence - 1,
                "head_hash": previous_head,
            }
        previous_hmac = seal["hmac_sha256"]
        previous_head = seal["head_hash"]
        expected_sequence += 1

    if not lines:
        return {"valid": False, "reason": "empty", "sequence": 0}
    return {
        "valid": True,
        "sequence": expected_sequence - 1,
        "head_hash": previous_head,
        "hmac_sha256": previous_hmac,
    }


def _write_seal_line(seal, initialize=False):
    secret = _seal_secret()
    seal["hmac_sha256"] = hmac.new(
        secret, _canonical_seal_payload(seal), hashlib.sha256
    ).hexdigest()
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    flags |= os.O_EXCL if initialize else os.O_APPEND
    descriptor = os.open(_SEAL_PATH, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8", closefd=False) as seal_file:
            seal_file.write(json.dumps(seal, sort_keys=True, separators=(",", ":")) + "\n")
            seal_file.flush()
            os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _locked_seal_operation(operation):
    os.makedirs(os.path.dirname(_SEAL_PATH), mode=0o700, exist_ok=True)
    lock_path = _SEAL_PATH + ".lock"
    descriptor = os.open(
        lock_path,
        os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return operation()
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _append_seal(head_hash, database_hashes):
    def append():
        chain = _read_seal_chain()
        if not chain["valid"]:
            raise RuntimeError("The external audit seal is not valid: %s" % chain["reason"])
        if chain["head_hash"] != _GENESIS_HASH and chain["head_hash"] not in database_hashes:
            raise RuntimeError("The sealed database head is missing from the current audit chain.")
        if chain["head_hash"] == head_hash:
            return chain["sequence"]
        seal = {
            "sequence": chain["sequence"] + 1,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "head_hash": head_hash,
            "prev_seal_hmac": chain["hmac_sha256"],
        }
        _write_seal_line(seal)
        return seal["sequence"]

    return _locked_seal_operation(append)


def _initialize_seal(head_hash):
    def initialize():
        if os.path.lexists(_SEAL_PATH):
            raise RuntimeError("The external audit seal already exists.")
        seal = {
            "sequence": 1,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "head_hash": head_hash,
            "prev_seal_hmac": "",
        }
        _write_seal_line(seal, initialize=True)
        return 1

    return _locked_seal_operation(initialize)


def _increment_failures():
    global _SEAL_FAILURES_TOTAL
    with _SEAL_LOCK:
        _SEAL_FAILURES_TOTAL += 1


def get_seal_failures_total():
    with _SEAL_LOCK:
        return _SEAL_FAILURES_TOTAL


def _seal_enabled(dbname):
    return os.environ.get("CARTES_AUDIT_SEAL_DATABASE") == dbname


def _sync_database_head(dbname, initialize=False):
    if not _seal_enabled(dbname):
        raise RuntimeError("External audit sealing is not enabled for this database.")
    registry = Registry(dbname)
    with registry.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            ("cartes_reduction.security.audit.chain",),
        )
        env = api.Environment(cursor, SUPERUSER_ID, {})
        audit_model = env["cartes.reduction.security.audit"]
        result = audit_model.verify_audit_chain()
        if not result["valid"]:
            raise RuntimeError("Cannot seal an invalid PostgreSQL audit chain.")
        hashes = audit_model.search([], order="id asc").mapped("entry_hash")
        if initialize:
            _initialize_seal(result["head_hash"])
        else:
            _append_seal(result["head_hash"], hashes)


def _postcommit_sync(dbname, initialize=False):
    try:
        _sync_database_head(dbname, initialize=initialize)
    except (OSError, RuntimeError, ValueError):
        _increment_failures()
        _logger.exception("External audit seal synchronization failed.")


class CartesSecurityAuditSealManager(models.Model):
    _inherit = "cartes.reduction.security.audit"

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        dbname = self.env.cr.dbname
        if not config["test_enable"] and _seal_enabled(dbname):
            self.env.cr.postcommit.add(
                lambda dbname=dbname: _postcommit_sync(dbname)
            )
        return records

    @api.model
    def verify_integrity(self):
        database = self.verify_audit_chain()
        if not database["valid"]:
            _increment_failures()
            return {**database, "postgres_valid": False, "seal_valid": False}
        seal = _read_seal_chain()
        database_hashes = self.sudo().search([], order="id asc").mapped("entry_hash")
        head_match = seal.get("head_hash") == database["head_hash"]
        sealed_head_exists = (
            seal.get("head_hash") == _GENESIS_HASH
            or seal.get("head_hash") in database_hashes
        )
        seal_valid = bool(seal.get("valid") and head_match and sealed_head_exists)
        if not seal_valid:
            _increment_failures()
        reason = seal.get("reason")
        if seal.get("valid") and not head_match:
            reason = "head_mismatch"
        elif seal.get("valid") and not sealed_head_exists:
            reason = "database_rollback"
        return {
            **database,
            "valid": seal_valid,
            "postgres_valid": True,
            "seal_valid": seal_valid,
            "seal": {
                **seal,
                "head_match": head_match,
                "sealed_head_exists": sealed_head_exists,
                "valid": seal_valid,
                "reason": reason,
            },
        }

    @api.model
    def action_initialize_external_seal(self):
        if not self.env.user.has_group("base.group_system"):
            raise AccessError("Only administrators can initialize the external audit seal.")
        dbname = self.env.cr.dbname
        if not _seal_enabled(dbname):
            raise UserError("Le scellement externe n’est pas activé pour cette base.")
        if os.path.lexists(_SEAL_PATH):
            raise UserError("Un sceau externe existe déjà; aucune réinitialisation n’est autorisée.")
        result = self.verify_audit_chain()
        if not result["valid"]:
            raise UserError("La chaîne PostgreSQL doit être valide avant l’initialisation.")
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            ("cartes_reduction.security.audit.chain",),
        )
        self.env.cr.postcommit.add(
            lambda dbname=dbname: _postcommit_sync(dbname, initialize=True)
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Initialisation du sceau externe",
                "message": "Le sceau sera créé après validation de la transaction.",
                "type": "warning",
                "sticky": True,
            },
        }
