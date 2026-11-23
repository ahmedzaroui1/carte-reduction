import hashlib
import json
import uuid

from odoo import api, fields, models
from odoo.exceptions import AccessError


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
            ("redemption", "Card redemption"),
            ("velocity_abuse", "VELOCITY_ABUSE"),
            ("ml_integrity_failure", "ML artifact integrity failure"),
            ("unauthorized_access", "Unauthorized access attempt"),
            ("tampered_token", "Tampered token blocked"),
            ("replay_blocked", "Replay blocked"),
        ],
        required=True,
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
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _latest_chained_event(self):
        return self.search([], limit=1, order="id desc")

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
            vals_for_hash = dict(vals)
            vals_for_hash["id"] = None
            vals["entry_hash"] = hashlib.sha256(
                (
                    prev_hash
                    + ":"
                    + json.dumps(
                        {
                            "event_at": str(vals_for_hash.get("event_at")),
                            "event_id": vals_for_hash.get("event_id"),
                            "event_type": vals_for_hash.get("event_type"),
                            "partner_id": vals_for_hash.get("partner_id") or 0,
                            "client_record_id": vals_for_hash.get("client_record_id") or 0,
                            "user_id": vals_for_hash.get("user_id") or 0,
                            "details": vals_for_hash.get("details") or "",
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    )
                ).encode("utf-8")
            ).hexdigest()
            prepared.append(vals)
            prev_hash = vals["entry_hash"]
        return super().create(prepared)

    @api.model
    def verify_audit_chain(self):
        records = self.sudo().search([], order="id asc")
        previous_hash = self.GENESIS_HASH
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
        return {"valid": True, "entries": len(records), "head_hash": previous_hash}

    def write(self, vals):
        raise AccessError("Security audit entries are immutable.")

    def unlink(self):
        raise AccessError("Security audit entries are immutable.")
