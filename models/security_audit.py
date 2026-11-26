import fcntl
import hashlib
import hmac
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone

from odoo import SUPERUSER_ID, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.modules.registry import Registry

from .security_utils import get_audit_seal_hmac_secret, _test_mode_enabled


_logger = logging.getLogger(__name__)
_SEAL_PATH = "/var/log/odoo-audit/head_hash.seal"
_SEAL_LOCK = threading.Lock()
_SEAL_FAILURES_TOTAL = 0
_SEAL_FIELDS = (
    "sequence",
    "timestamp",
    "head_hash",
    "prev_seal_hmac",
)


def _seal_payload(seal):
    return {field: seal[field] for field in _SEAL_FIELDS}


def _read_seal_chain():
    if os.path.islink(_SEAL_PATH) or not os.path.isfile(_SEAL_PATH):
        return {"valid": False, "reason": "missing", "sequence": 0}
    try:
        secret = get_audit_seal_hmac_secret()
        with open(_SEAL_PATH, "r", encoding="utf-8") as seal_file:
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
            head_hash = seal["head_hash"]
            if not isinstance(head_hash, str) or not head_hash:
                raise ValueError("invalid sealed head hash")
            if not isinstance(seal["timestamp"], str):
                raise ValueError("invalid seal timestamp")
            if not isinstance(seal["hmac_sha256"], str):
                raise ValueError("invalid seal signature")
            expected_hmac = hmac.new(
                secret,
                json.dumps(
                    _seal_payload(seal),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(seal["hmac_sha256"], expected_hmac):
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
        previous_head = head_hash
        expected_sequence += 1

    if not lines:
        return {"valid": False, "reason": "empty", "sequence": 0}
    return {
        "valid": True,
        "sequence": expected_sequence - 1,
        "head_hash": previous_head,
        "hmac_sha256": previous_hmac,
    }


def _write_seal_line(seal, create=False):
    secret = get_audit_seal_hmac_secret()
    signature = hmac.new(
        secret,
        json.dumps(
            _seal_payload(seal),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    seal["hmac_sha256"] = signature
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if create else os.O_APPEND)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(_SEAL_PATH, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8", closefd=False) as seal_file:
            seal_file.write(
                json.dumps(seal, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                + "\n"
            )
            seal_file.flush()
            os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return seal["hmac_sha256"]


def _locked_seal_operation(operation):
    os.makedirs(os.path.dirname(_SEAL_PATH), mode=0o700, exist_ok=True)
    lock_path = f"{_SEAL_PATH}.lock"
    lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(lock_descriptor, 0o600)
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        return operation()
    finally:
        fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
        os.close(lock_descriptor)


def _append_external_seal(head_hash, database_hashes):
    def append():
        chain = _read_seal_chain()
        if not chain["valid"]:
            raise RuntimeError(f"External audit seal is not valid: {chain['reason']}")
        if chain["head_hash"] != "CARTES_REDUCTION_SECURITY_AUDIT_GENESIS_V1":
            if chain["head_hash"] not in database_hashes:
                raise RuntimeError("External audit seal head is not in the current database chain.")
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


def _initialize_external_seal(head_hash):
    def initialize():
        if os.path.exists(_SEAL_PATH):
            raise RuntimeError("External audit seal already exists; initialization is one-time only.")
        seal = {
            "sequence": 1,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "head_hash": head_hash,
            "prev_seal_hmac": "",
        }
        _write_seal_line(seal, create=True)
        return 1

    return _locked_seal_operation(initialize)


def _record_seal_failure(error):
    _increment_seal_failure()
    _logger.exception("External audit seal operation failed: %s", error)


def _increment_seal_failure():
    global _SEAL_FAILURES_TOTAL
    with _SEAL_LOCK:
        _SEAL_FAILURES_TOTAL += 1


def get_seal_failures_total():
    with _SEAL_LOCK:
        return _SEAL_FAILURES_TOTAL


def _seal_enabled_for_database(dbname):
    return os.environ.get("CARTES_AUDIT_SEAL_DATABASE") == dbname


def _sync_committed_database_head(dbname, initialize=False):
    if not _seal_enabled_for_database(dbname):
        raise RuntimeError("External audit sealing is not enabled for this database.")
    registry = Registry(dbname)
    with registry.cursor() as cursor:
        env = api.Environment(cursor, SUPERUSER_ID, {})
        env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            ("cartes_reduction.security.audit.chain",),
        )
        audit_model = env["cartes.reduction.security.audit"]
        result = audit_model.verify_audit_chain()
        if not result["valid"]:
            raise RuntimeError("Cannot seal an invalid PostgreSQL audit chain.")
        if initialize:
            _initialize_external_seal(result["head_hash"])
        else:
            _append_external_seal(result["head_hash"], result["chain_hashes"])


def _run_postcommit_seal_sync(dbname, initialize=False):
    try:
        _sync_committed_database_head(dbname, initialize=initialize)
    except (OSError, RuntimeError, ValueError) as error:
        _record_seal_failure(error)
        raise


class CartesSecurityAudit(models.Model):
    _name = "cartes.reduction.security.audit"
    _description = "Cartes de Réduction Security Audit"
    _order = "event_at desc, id desc"

    GENESIS_HASH = "CARTES_REDUCTION_SECURITY_AUDIT_GENESIS_V1"

    event_at = fields.Datetime(required=True, default=fields.Datetime.now, index=True)
    event_type = fields.Selection(
        [
            ("cin_reveal", "CIN revealed"),
            ("pin_set", "Personal PIN changed"),
            ("pin_failed", "Personal PIN verification failed"),
            ("pos_otp_failed", "POS OTP verification failed"),
            ("otp_expired", "OTP expired"),
            ("redemption", "Card redemption"),
            ("velocity_abuse", "Velocity abuse"),
            ("ml_integrity_failure", "ML artifact integrity failure"),
            ("unauthorized_access", "Unauthorized access attempt"),
            ("idor_blocked", "IDOR blocked"),
            ("injection_blocked", "Injection blocked"),
            ("tampered_token", "Tampered token blocked"),
            ("hmac_invalid", "Invalid HMAC"),
            ("replay_blocked", "Replay blocked"),
            ("double_spend_blocked", "Double spend blocked"),
            ("seal_verify", "Audit seal verification"),
            ("login", "Login"),
            ("logout", "Logout"),
            ("login_failed", "Login failed"),
        ],
        required=True,
        index=True,
    )
    actor_scope = fields.Selection(
        [
            ("pos_cashier", "POS cashier"),
            ("ecommerce_client", "E-commerce client"),
            ("administrator", "Administration"),
            ("unknown", "Unknown"),
        ],
        index=True,
    )
    partner_id = fields.Integer(index=True)
    client_record_id = fields.Integer(index=True)
    user_id = fields.Integer(index=True)
    details = fields.Text()
    event_id = fields.Char(readonly=True, required=True, copy=False, index=True)
    prev_hash = fields.Char(readonly=True, index=True)
    entry_hash = fields.Char(readonly=True, index=True)

    def _canonical_payload(self, values, prev_hash):
        event_at = values.get("event_at") or fields.Datetime.now()
        if hasattr(event_at, "isoformat"):
            event_at = event_at.isoformat()
        payload = {
            "id": values.get("id"),
            "prev_hash": prev_hash,
            "event_at": str(event_at),
            "event_type": values.get("event_type"),
            "partner_id": values.get("partner_id") or 0,
            "client_record_id": values.get("client_record_id") or 0,
            "user_id": values.get("user_id") or 0,
            "details": values.get("details") or "",
        }
        if values.get("actor_scope"):
            payload["actor_scope"] = values["actor_scope"]
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _latest_chained_event(self):
        return self.search([], limit=1, order="id desc")

    def _actor_scope_for_user(self, user_id):
        user = self.env["res.users"].sudo().browse(user_id).exists() if user_id else False
        if not user:
            return "unknown"
        if user.has_group("base.group_system") or user.has_group(
            "cartes_reduction.cartes_manager_group"
        ):
            return "administrator"
        if user.has_group("point_of_sale.group_pos_user"):
            return "pos_cashier"
        if user.has_group("base.group_portal"):
            return "ecommerce_client"
        return "unknown"

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError("Security audit entries can only be created internally.")
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            ("cartes_reduction.security.audit.chain",),
        )
        prepared = []
        previous = self._latest_chained_event()
        prev_hash = previous.entry_hash if previous and previous.entry_hash else self.GENESIS_HASH
        for vals in vals_list:
            vals = dict(vals)
            vals.setdefault("event_at", fields.Datetime.now())
            vals["event_id"] = str(uuid.uuid4())
            vals["prev_hash"] = prev_hash
            vals["actor_scope"] = vals.get("actor_scope") or self._actor_scope_for_user(
                vals.get("user_id") or self.env.uid
            )
            vals_for_hash = dict(vals)
            vals_for_hash["id"] = None
            payload = {
                "event_at": str(vals_for_hash.get("event_at")),
                "event_id": vals_for_hash.get("event_id"),
                "event_type": vals_for_hash.get("event_type"),
                "partner_id": vals_for_hash.get("partner_id") or 0,
                "client_record_id": vals_for_hash.get("client_record_id") or 0,
                "user_id": vals_for_hash.get("user_id") or 0,
                "details": vals_for_hash.get("details") or "",
                "actor_scope": vals_for_hash["actor_scope"],
            }
            vals["entry_hash"] = hashlib.sha256(
                (
                    prev_hash
                    + ":"
                    + json.dumps(
                        payload,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    )
                ).encode("utf-8")
            ).hexdigest()
            prepared.append(vals)
            prev_hash = vals["entry_hash"]
        records = super().create(prepared)
        if not _test_mode_enabled() and _seal_enabled_for_database(self.env.cr.dbname):
            dbname = self.env.cr.dbname
            self.env.cr.postcommit.add(
                lambda dbname=dbname: _run_postcommit_seal_sync(dbname)
            )
        return records

    @api.model
    def verify_audit_chain(self):
        records = self.sudo().search([], order="id asc")
        previous_hash = self.GENESIS_HASH
        chain_hashes = []
        for record in records:
            payload = {
                "event_at": str(record.event_at),
                "event_id": record.event_id,
                "event_type": record.event_type,
                "partner_id": record.partner_id or 0,
                "client_record_id": record.client_record_id or 0,
                "user_id": record.user_id or 0,
                "details": record.details or "",
            }
            if record.actor_scope:
                payload["actor_scope"] = record.actor_scope
            canonical = (
                previous_hash
                + ":"
                + json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            )
            expected_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if record.prev_hash != previous_hash:
                return {"valid": False, "reason": "broken_link", "id": record.id}
            if record.entry_hash != expected_hash:
                return {"valid": False, "reason": "payload_or_hash_modified", "id": record.id}
            previous_hash = record.entry_hash
            chain_hashes.append(previous_hash)
        return {
            "valid": True,
            "entries": len(records),
            "head_hash": previous_hash,
            "chain_hashes": chain_hashes,
        }

    @api.model
    def verify_external_seal(self, db_head_hash=None):
        if not _seal_enabled_for_database(self.env.cr.dbname):
            return {
                "valid": False,
                "reason": "database_sealing_disabled",
                "sequence": 0,
                "head_match": False,
            }
        if db_head_hash is None:
            db_result = self.verify_audit_chain()
            if not db_result["valid"]:
                return {
                    "valid": False,
                    "reason": "postgres_chain_invalid",
                    "sequence": 0,
                    "head_match": False,
                }
            db_head_hash = db_result["head_hash"]
        seal = _read_seal_chain()
        head_match = seal.get("head_hash") == db_head_hash
        return {
            **seal,
            "head_match": head_match,
            "valid": bool(seal.get("valid") and head_match),
            "reason": seal.get("reason") if not seal.get("valid") else (
                "head_mismatch" if not head_match else False
            ),
        }

    @api.model
    def verify_integrity(self):
        database = self.verify_audit_chain()
        if not database["valid"]:
            _increment_seal_failure()
            _logger.error(
                "PostgreSQL audit chain verification failed: %s",
                database.get("reason"),
            )
            return {**database, "postgres_valid": False, "seal_valid": False}
        seal = self.verify_external_seal(database["head_hash"])
        if not seal["valid"]:
            _increment_seal_failure()
            _logger.error(
                "External audit seal verification failed: %s",
                seal.get("reason"),
            )
        return {
            **database,
            "valid": bool(database["valid"] and seal["valid"]),
            "postgres_valid": database["valid"],
            "seal_valid": seal["valid"],
            "seal": seal,
        }

    @api.model
    def action_verify_integrity(self):
        if not self.env.user.has_group("base.group_system"):
            raise AccessError("Only administrators can verify the external audit seal.")
        result = self.verify_integrity()
        message = (
            "La chaîne PostgreSQL et le sceau externe sont intègres."
            if result["valid"]
            else f"Échec de vérification : {result.get('reason') or result['seal'].get('reason')}."
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Vérification d’intégrité SHA-256",
                "message": message,
                "type": "success" if result["valid"] else "danger",
                "sticky": not result["valid"],
            },
        }

    @api.model
    def action_initialize_external_seal(self):
        if not self.env.user.has_group("base.group_system"):
            raise AccessError("Only administrators can initialize the external audit seal.")
        if not _seal_enabled_for_database(self.env.cr.dbname):
            raise UserError("Le scellement externe n’est pas activé pour cette base.")
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            ("cartes_reduction.security.audit.chain",),
        )
        result = self.verify_audit_chain()
        if not result["valid"]:
            raise UserError("La chaîne PostgreSQL doit être valide avant l’initialisation du sceau.")
        dbname = self.env.cr.dbname
        self.env.cr.postcommit.add(
            lambda dbname=dbname: _run_postcommit_seal_sync(dbname, initialize=True)
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Initialisation du sceau externe",
                "message": "L’initialisation sera effectuée après validation de la transaction.",
                "type": "warning",
                "sticky": True,
            },
        }

    def write(self, vals):
        raise AccessError("Security audit entries are immutable.")

    def unlink(self):
        raise AccessError("Security audit entries are immutable.")
