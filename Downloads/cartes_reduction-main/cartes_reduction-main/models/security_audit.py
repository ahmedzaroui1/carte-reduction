from odoo import api, fields, models, SUPERUSER_ID
from odoo.exceptions import AccessError, UserError


class CartesReductionSecurityAudit(models.Model):
    _name = 'cartes_reduction.security.audit'
    _description = 'Cartes de Réduction Security Audit'
    _order = 'event_date desc, id desc'

    event_date = fields.Datetime(default=fields.Datetime.now, readonly=True, required=True)
    user_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    card_ref = fields.Char(string="Card / Token Reference", readonly=True)
    channel = fields.Selection([
        ('pos', 'Point of Sale'),
        ('sale', 'Sales Order'),
        ('website', 'E-Commerce'),
        ('api', 'External/Shell'),
    ], required=True, readonly=True)
    event_type = fields.Selection([
        ('valid_redeem', 'Valid Redemption'),
        ('tampered_token', 'Tampered Signature Blocked'),
        ('replay_blocked', 'Replay / Double-Spend Blocked'),
        ('unauthorized_access', 'Unauthorized Access Attempt'),
        ('expired_or_inactive', 'Expired or Inactive Card Blocked'),
        ('card_issued', 'Card Cryptographically Signed'),
    ], required=True, readonly=True)
    severity = fields.Selection([
        ('info', 'INFO'),
        ('warning', 'WARNING'),
        ('critical', 'CRITICAL'),
    ], required=True, readonly=True)
    details = fields.Text(readonly=True)

    @api.model
    def log_security_event(
        self, event_type, severity, channel, card_ref=None, details=None, actor_uid=None
    ):
        if not self.env.su:
            raise AccessError("Only trusted server code can write security audit events.")
        valid_channels = {'pos', 'sale', 'website', 'api'}
        valid_events = {
            'valid_redeem', 'tampered_token', 'replay_blocked',
            'unauthorized_access', 'expired_or_inactive', 'card_issued',
        }
        valid_severities = {'info', 'warning', 'critical'}
        if event_type not in valid_events or severity not in valid_severities or channel not in valid_channels:
            raise ValueError("Invalid security audit event values.")

        with self.pool.cursor() as audit_cr:
            audit_env = api.Environment(audit_cr, SUPERUSER_ID, {})
            audit_env['cartes_reduction.security.audit'].with_context(
                _security_audit_internal=True
            ).create({
                'user_id': actor_uid or SUPERUSER_ID,
                'event_type': event_type,
                'severity': severity,
                'channel': channel,
                'card_ref': str(card_ref) if card_ref else False,
                'details': details,
            })
            audit_cr.commit()

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su or not self.env.context.get('_security_audit_internal'):
            raise UserError("Security audit records can only be created by the audit service.")
        return super().create(vals_list)

    def write(self, vals):
        raise UserError(
            "VIOLATION DE SÉCURITÉ : Les journaux d'audit sont immuables et ne peuvent ni être modifiés ni supprimés."
        )

    def unlink(self):
        raise UserError(
            "VIOLATION DE SÉCURITÉ : Les journaux d'audit sont immuables et ne peuvent ni être modifiés ni supprimés."
        )
