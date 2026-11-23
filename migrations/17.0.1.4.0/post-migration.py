import hashlib
import json
import uuid


GENESIS_HASH = "CARTES_REDUCTION_SECURITY_AUDIT_GENESIS_V1"


def migrate(cr, version):
    cr.execute(
        """
        SELECT id, event_at, event_type, partner_id, client_record_id, user_id, details
          FROM cartes_reduction_security_audit
         ORDER BY id ASC
        """
    )
    previous_hash = GENESIS_HASH
    for row in cr.fetchall():
        record_id, event_at, event_type, partner_id, client_record_id, user_id, details = row
        event_identifier = str(uuid.uuid4())
        payload = {
            "event_at": str(event_at),
            "event_id": event_identifier,
            "event_type": event_type,
            "partner_id": partner_id or 0,
            "client_record_id": client_record_id or 0,
            "user_id": user_id or 0,
            "details": details or "",
        }
        entry_hash = hashlib.sha256(
            (
                previous_hash
                + ":"
                + json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            ).encode("utf-8")
        ).hexdigest()
        cr.execute(
            """
            UPDATE cartes_reduction_security_audit
               SET event_id = %s, prev_hash = %s, entry_hash = %s
             WHERE id = %s
            """,
            (event_identifier, previous_hash, entry_hash, record_id),
        )
        previous_hash = entry_hash
