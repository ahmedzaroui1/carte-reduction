from datetime import date, timedelta
import base64
import hashlib
import hmac
import io
import logging
import math
import re
import secrets
import uuid

import psycopg2
from psycopg2.errors import LockNotAvailable

from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class AttributionCarte(models.Model):
    _name = 'cartes.attribution'
    _description = "Attribution de Carte à un Client"

    # Relation avec le modèle res.partner
    client_id = fields.Many2one('res.partner', string="Client", required=True)
    loyalty_program_id = fields.Many2many('loyalty.program', string="Programme de Fidélité")
    # Champ related pour faciliter les calculs
    max_discount = fields.Float(related='loyalty_program_id.reward_ids.discount',
                                string="Remise maximale",
                                store=True)
    # Champs relatifs au client (issus de res.partner)
    client_name = fields.Char(related="client_id.name", string="Nom", readonly=True)
    client_num_telephone = fields.Char(related="client_id.phone", string="Numéro de Téléphone", readonly=True)
    client_email = fields.Char(related="client_id.email", string="Email", readonly=True)
    state = fields.Selection([
        ('draft', 'Brouillon'),
        ('validated', 'Validé'),
        ('expired', 'Expiré'),
        ('renouvelé', 'Renouvelé'),
    ], string="Statut", default="draft")
    def action_cartes_attribution_list(self):
        """ Action pour afficher la liste des clients avec leurs cartes """
        return {
            'type': 'ir.actions.act_window',
            'name': 'Liste des Clients avec Cartes',
            'view_mode': 'tree,form',
            'res_model': 'cartes.attribution',
            'target': 'current',
        }
    # Champs relatifs à l'attribution de carte
    carte_ids = fields.Many2many('cartes.reduction', string="Cartes de Réduction", required=True)
    date_attribution = fields.Date(string="Date d'Attribution", default=fields.Date.context_today)
    date_expiration = fields.Date(string="Date d'Expiration", required=True)
    nonce = fields.Char(
        string="Security Nonce", default=lambda self: str(uuid.uuid4()),
        readonly=True, copy=False, groups='cartes_reduction.cartes_manager_group',
    )
    signed_token = fields.Char(
        string="Cryptographic Token", readonly=True, copy=False,
        groups='cartes_reduction.cartes_manager_group',
    )
    security_qr_code = fields.Binary(
        string="Signed Security QR", readonly=True, copy=False, attachment=True,
        groups='cartes_reduction.cartes_manager_group',
    )

    @api.model
    def _security_hmac_secret(self):
        self.env.cr.execute("SELECT pg_advisory_xact_lock(%s)", [732194816024])
        parameters = self.env['ir.config_parameter'].sudo()
        secret = parameters.get_param('cartes_reduction.hmac_secret')
        if not secret:
            secret = secrets.token_hex(32)
            parameters.set_param('cartes_reduction.hmac_secret', secret)
        if not re.fullmatch(r'[0-9a-f]{64}', secret):
            raise UserError("The configured loyalty-token signing key is invalid.")
        return bytes.fromhex(secret)

    def _security_card(self):
        self.ensure_one()
        card = self.carte_ids.sorted('id')[:1]
        if not card:
            raise ValidationError("A signed token requires an assigned discount card.")
        return card

    def _security_token_payload(self):
        self.ensure_one()
        card = self._security_card()
        return "v1:%s:%s:%.4f:%s:%s" % (
            self.id,
            self.client_id.id,
            card.discount or 0.0,
            fields.Date.to_string(self.date_expiration),
            self.nonce,
        )

    def _sign_security_token(self):
        for attribution in self:
            secure_record = attribution.sudo()
            if not secure_record.nonce:
                super(AttributionCarte, secure_record).write({
                    'nonce': str(uuid.uuid4()),
                })
            payload = secure_record._security_token_payload()
            mac = hmac.new(
                secure_record._security_hmac_secret(), payload.encode('utf-8'), hashlib.sha256
            ).hexdigest()
            token = "%s.%s" % (payload, mac)
            qr_image = False
            try:
                import qrcode
                image = qrcode.make(token)
                buffer = io.BytesIO()
                image.save(buffer, format='PNG')
                qr_image = base64.b64encode(buffer.getvalue())
            except ImportError:
                _logger.warning("Optional qrcode package is unavailable; signed token QR was not generated.")

            super(AttributionCarte, secure_record).write({
                'signed_token': token,
                'security_qr_code': qr_image,
            })

    @api.model_create_multi
    def create(self, vals_list):
        protected = {'nonce', 'signed_token', 'security_qr_code'}
        if any(protected.intersection(vals) for vals in vals_list):
            raise UserError("Security token fields are managed by the card security service.")
        records = super().create(vals_list)
        records._sign_security_token()
        for record in records:
            self.env['cartes_reduction.security.audit'].sudo().log_security_event(
                'card_issued', 'info', 'api', record.id, "Card token signed.",
                actor_uid=self.env.uid,
            )
        return records

    def write(self, vals):
        protected = {'nonce', 'signed_token', 'security_qr_code'}
        if protected.intersection(vals):
            raise UserError("Security token fields are managed by the card security service.")

        old_states = {record.id: record.state for record in self}
        result = super().write(vals)
        if {'client_id', 'carte_ids', 'date_expiration', 'state'}.intersection(vals):
            self._sign_security_token()
            for record in self:
                if (
                    record.state in ('validated', 'renouvelé')
                    and old_states.get(record.id) not in ('validated', 'renouvelé')
                ):
                    self.env['cartes_reduction.security.audit'].sudo().log_security_event(
                        'card_issued', 'info', 'api', record.id,
                        "Card activated and token signed.",
                        actor_uid=self.env.uid,
                    )
        return result

    def action_refresh_security_token(self):
        if not self.env.user.has_group('cartes_reduction.cartes_manager_group'):
            raise UserError("Only a Cartes Manager can issue or rotate a security token.")
        for record in self:
            super(AttributionCarte, record.sudo()).write({
                'nonce': str(uuid.uuid4()),
            })
            record._sign_security_token()
            self.env['cartes_reduction.security.audit'].sudo().log_security_event(
                'card_issued', 'info', 'api', record.id, "Card token manually issued or rotated.",
                actor_uid=self.env.uid,
            )
        return True

    @api.model
    def _reject_security_token(self, event_type, severity, channel, card_ref, message, details):
        self.env['cartes_reduction.security.audit'].sudo().log_security_event(
            event_type, severity, channel, card_ref, details, actor_uid=self.env.uid
        )
        raise UserError(message)

    @api.model
    def verify_and_redeem_token(
        self, token, channel='sale', order_ref='', consume_nonce=True, expected_partner_id=None
    ):
        if channel not in ('pos', 'sale', 'website', 'api'):
            raise ValueError("Unsupported loyalty-token redemption channel.")

        if not isinstance(token, str) or len(token) > 512:
            self._reject_security_token(
                'tampered_token', 'critical', channel, False,
                "The security token is malformed.", "Malformed token rejected."
            )
        parts = token.rsplit('.', 1)
        payload = parts[0] if len(parts) == 2 else ''
        provided_mac = parts[1] if len(parts) == 2 else ''
        payload_parts = payload.split(':')
        card_ref = (
            payload_parts[1]
            if len(payload_parts) > 1 and re.fullmatch(r'[0-9]{1,20}', payload_parts[1])
            else False
        )
        if (
            len(payload_parts) != 6
            or payload_parts[0] != 'v1'
            or not re.fullmatch(r'[0-9a-f]{64}', provided_mac)
        ):
            self._reject_security_token(
                'tampered_token', 'critical', channel, card_ref,
                "The security token is malformed.", "Malformed token rejected."
            )
        try:
            card_id = int(payload_parts[1])
            partner_id = int(payload_parts[2])
            token_rate = float(payload_parts[3])
            token_expiry = fields.Date.from_string(payload_parts[4])
            token_nonce = payload_parts[5]
            if (
                not math.isfinite(token_rate)
                or not token_nonce
                or str(uuid.UUID(token_nonce)) != token_nonce
                or fields.Date.to_string(token_expiry) != payload_parts[4]
                or ("%.4f" % token_rate) != payload_parts[3]
            ):
                raise ValueError("empty nonce")
        except (TypeError, ValueError):
            self._reject_security_token(
                'tampered_token', 'critical', channel, card_ref,
                "The security token is malformed.", "Invalid token payload rejected."
            )

        expected_mac = hmac.new(
            self._security_hmac_secret(), payload.encode('utf-8'), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected_mac, provided_mac):
            self._reject_security_token(
                'tampered_token', 'critical', channel, card_id,
                "The security token signature is invalid.", "Invalid token signature rejected."
            )

        try:
            with self.env.cr.savepoint():
                self.env.cr.execute(
                    "SELECT id FROM %s WHERE id = %%s FOR UPDATE NOWAIT" % self._table,
                    [card_id],
                )
                locked = self.env.cr.fetchone()
        except (psycopg2.OperationalError, LockNotAvailable):
            self._reject_security_token(
                'replay_blocked', 'critical', channel, card_id,
                "This card is being redeemed concurrently; retry after verifying its status.",
                "Concurrent redemption blocked by row lock."
            )
        if not locked:
            self._reject_security_token(
                'tampered_token', 'critical', channel, card_id,
                "The security token references an unknown card.", "Unknown card reference rejected."
            )

        attribution = self.browse(card_id).sudo()
        attribution.invalidate_recordset()
        if not hmac.compare_digest(attribution.nonce or '', token_nonce):
            self._reject_security_token(
                'replay_blocked', 'critical', channel, card_id,
                "This security token has already been used.", "Previously consumed nonce rejected."
            )
        current_payload = attribution._security_token_payload()
        if (
            attribution.client_id.id != partner_id
            or attribution.date_expiration != token_expiry
            or abs(float(current_payload.split(':')[3]) - token_rate) > 0.00005
            or not hmac.compare_digest(attribution.signed_token or '', token)
        ):
            self._reject_security_token(
                'tampered_token', 'critical', channel, card_id,
                "The security token no longer matches the card.", "Token does not match current card state."
            )
        if expected_partner_id and attribution.client_id.id != expected_partner_id:
            self._reject_security_token(
                'unauthorized_access', 'warning', channel, card_id,
                "This token belongs to a different customer.",
                "Token redemption rejected because the order customer did not match."
            )
        card = attribution._security_card()
        today = fields.Date.today()
        if (
            attribution.state not in ('validated', 'renouvelé')
            or not attribution.date_expiration
            or attribution.date_expiration < today
            or not card.active
            or card.state == 'archived'
            or (card.date_debut and card.date_debut > today)
            or (card.date_expiration and card.date_expiration < today)
        ):
            self._reject_security_token(
                'expired_or_inactive', 'warning', channel, card_id,
                "The discount card is inactive or expired.", "Inactive or expired card redemption rejected."
            )

        if consume_nonce:
            super(AttributionCarte, attribution).write({
                'nonce': str(uuid.uuid4()),
            })
            attribution._sign_security_token()
        detail = "Token redeemed."
        if order_ref:
            detail = "Token redeemed for order %s." % str(order_ref)[:128]
        self.env['cartes_reduction.security.audit'].sudo().log_security_event(
            'valid_redeem', 'info', channel, card_id, detail, actor_uid=self.env.uid
        )
        return attribution

    def renouveler_carte(self):
        """Renouvelle les cartes attribuées à un client"""
        for record in self:
            if not record.carte_ids:
                raise ValidationError("Aucune carte attribuée à renouveler !")

            cartes_expirees = record.carte_ids.filtered(lambda c: c.date_expiration < fields.Date.today())

            if not cartes_expirees:
                raise ValidationError("Toutes les cartes sont encore valides !")

            for carte in cartes_expirees:
                nouvelle_date_expiration = fields.Date.today() + timedelta(days=365)
                carte.write({
                    'date_debut': fields.Date.today(),
                    'date_expiration': nouvelle_date_expiration,
                    'state': 'active'
                })

            record.write({'date_expiration': max(record.carte_ids.mapped('date_expiration'))})
    @api.onchange('carte_ids')
    def _check_nombre_utilisateurs(self):
        """ Vérifie si le nombre d'utilisateurs n'a pas dépassé la limite. """
        for carte in self.carte_ids:
            if carte.nombre_utilisateurs >= carte.nombre_utilisateurs_max:
                raise UserError("Le nombre d'utilisateurs autorisés pour cette carte a été atteint.")
            else:
                carte.nombre_utilisateurs += 1

    @api.onchange('date_attribution', 'date_expiration')
    def _check_dates(self):
        """ Vérifie si la date d'expiration est postérieure à la date d'attribution. """
        if self.date_expiration and self.date_attribution and self.date_expiration < self.date_attribution:
            raise UserError("La date d'expiration ne peut pas être antérieure à la date d'attribution.")

    def action_attribuer(self):
        """ Méthode pour effectuer l'attribution de carte. """
        for record in self:
            record.write({'state': 'attribué'})  # Exemple d'une mise à jour d'état



    @api.depends('client_id', 'carte_ids')
    def _compute_display_name(self):
        for record in self:
            carte_names = ", ".join(record.carte_ids.mapped('name'))
            record.display_name = f"{record.client_id.name} - {carte_names}"

    display_name = fields.Char(compute="_compute_display_name", store=True)

    @api.model
    def action_renouveler(self):
        for record in self:
            # Mettez à jour la date d'expiration
            record.date_expiration = fields.Date.today() + timedelta(days=365)  # Par exemple, ajouter 1 an
            record.state = 'active'  # Changez l'état de la carte à active

    @api.depends('date_expiration')
    def _compute_message(self):
        for record in self:
            if record.date_expiration and record.date_expiration < fields.Date.today():
                record.message = "Aucune carte expirée pour le moment"
            else:
                record.message = False

    message = fields.Char(compute='_compute_message', store=False)

    def action_ouvrir_wizard_renouvellement(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Renouveler Carte',
            'res_model': 'wizard.renouvellement.carte',
            'view_mode': 'form',
            'view_id': self.env.ref('cartes_reduction.view_wizard_renouvellement_form').id,
            'target': 'new',
            'context': {'default_attribution_id': self.id},
        }

    def _cron_check_expired_cards(self):
        """Méthode appelée par le cron pour vérifier les cartes expirées"""
        expired_cards = self.search([
            ('date_expiration', '<', fields.Date.today()),
            ('state', '=', 'validated')
        ])
        expired_cards._notify_expired_cards()

    def _notify_expired_cards(self):
        """Notifie les clients dont les cartes sont expirées"""
        Mail = self.env['mail.mail']
        Notification = self.env['notifications.client']

        for card in self:
            # Envoyer l'email
            mail_values = {
                'subject': f"Votre carte {card.carte_ids.name} est expirée",
                'body_html': f"""
                    <div>
                        <p>Bonjour {card.client_id.name},</p>
                        <p>Votre carte {card.carte_ids.name} est expirée depuis le {card.date_expiration}.</p>
                        <p>Pour renouveler, contactez notre service client.</p>
                        <p>Cordialement,<br/>L'équipe {self.env.company.name}</p>
                    </div>
                """,
                'email_to': card.client_id.email,
                'email_from': self.env.user.email,
            }
            mail = Mail.create(mail_values)
            mail.send()

            # Créer la notification
            Notification.create({
                'name': f"Carte {card.carte_ids.name} expirée",
                'message': f"Notification envoyée à {card.client_id.email}",
                'client_id': card.client_id.id,
                'date': fields.Datetime.now(),
            })

            # Mettre à jour le statut
            card.write({'state': 'expired'})

    #hedhi mtee3 gift card
