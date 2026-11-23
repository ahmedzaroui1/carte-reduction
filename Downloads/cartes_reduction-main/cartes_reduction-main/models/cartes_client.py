import base64
import binascii
import hashlib
import hmac

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class Client(models.Model):
    _name = 'cartes.client'
    _description = "Client"

    _sql_constraints = [
        ('cin_digest_unique', 'unique(cin_digest)', 'The CIN must be unique.'),
    ]

    name = fields.Char(string="Nom", required=True)
    prenom = fields.Char(string="Prénom", required=True)
    num_telephone = fields.Char(string="Numéro de Téléphone", required=True)
    cin_encrypted = fields.Char(
        string="Encrypted CIN", readonly=True, copy=False,
        groups='cartes_reduction.cartes_manager_group',
    )
    cin_digest = fields.Char(
        readonly=True, copy=False, index=True,
        groups='cartes_reduction.cartes_manager_group',
    )
    cin = fields.Char(
        string="CIN", compute='_compute_cin', inverse='_inverse_cin',
        required=True, groups='cartes_reduction.cartes_manager_group',
    )
    cin_masked = fields.Char(string="CIN masqué", compute='_compute_cin_masked')
    carte_ids = fields.Many2many('cartes.reduction', string="Cartes de Réduction")

    @api.model
    def _cin_fernet(self):
        self.env.cr.execute("SELECT pg_advisory_xact_lock(%s)", [732194816025])
        parameters = self.env['ir.config_parameter'].sudo()
        key = parameters.get_param('cartes_reduction.fernet_key')
        if not key:
            try:
                from cryptography.fernet import Fernet
            except ImportError as exc:
                raise UserError(
                    "The cryptography package is required to protect customer CIN data."
                ) from exc
            key = Fernet.generate_key().decode('ascii')
            parameters.set_param('cartes_reduction.fernet_key', key)
        try:
            from cryptography.fernet import Fernet
            cipher = Fernet(key.encode('ascii'))
        except (ImportError, ValueError, UnicodeEncodeError, binascii.Error) as exc:
            raise UserError(
                "The configured CIN encryption key is unavailable or invalid; CIN access is blocked."
            ) from exc
        key_bytes = base64.urlsafe_b64decode(key.encode('ascii'))
        digest_key = hashlib.sha256(key_bytes + b'cartes.client.cin.digest').digest()
        return cipher, digest_key

    @api.model
    def _encrypt_cin(self, value):
        if not isinstance(value, str) or not value.strip():
            raise ValidationError("A CIN value is required.")
        normalized = value.strip()
        cipher, digest_key = self._cin_fernet()
        encrypted = cipher.encrypt(normalized.encode('utf-8')).decode('ascii')
        digest = hmac.new(digest_key, normalized.encode('utf-8'), hashlib.sha256).hexdigest()
        return encrypted, digest

    @api.model
    def init(self):
        self.env.cr.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'cartes_client' AND column_name = 'cin'"
        )
        if not self.env.cr.fetchone():
            return
        self.env.cr.execute(
            "SELECT id, cin FROM cartes_client "
            "WHERE cin IS NOT NULL AND COALESCE(cin_encrypted, '') = ''"
        )
        legacy_rows = self.env.cr.fetchall()
        for record_id, plaintext in legacy_rows:
            encrypted, digest = self._encrypt_cin(plaintext)
            self.env.cr.execute(
                "UPDATE cartes_client SET cin_encrypted = %s, cin_digest = %s WHERE id = %s",
                [encrypted, digest, record_id],
            )
        self.env.cr.execute("ALTER TABLE cartes_client DROP COLUMN cin")

    @api.depends('cin_encrypted')
    def _compute_cin(self):
        for record in self:
            if not record.cin_encrypted:
                record.cin = False
                continue
            try:
                from cryptography.fernet import InvalidToken
            except ImportError as exc:
                raise UserError("The cryptography package is required to read CIN data.") from exc
            cipher, _digest_key = record._cin_fernet()
            try:
                record.cin = cipher.decrypt(record.cin_encrypted.encode('ascii')).decode('utf-8')
            except (ValueError, UnicodeEncodeError, InvalidToken) as exc:
                raise UserError("Stored CIN data could not be decrypted; access is blocked.") from exc

    def _inverse_cin(self):
        for record in self:
            encrypted, digest = record._encrypt_cin(record.cin)
            super(Client, record.sudo()).write({
                'cin_encrypted': encrypted,
                'cin_digest': digest,
            })

    @api.depends('cin_encrypted')
    def _compute_cin_masked(self):
        for record in self:
            secure_record = record.sudo()
            if not secure_record.cin_encrypted:
                record.cin_masked = False
                continue
            try:
                from cryptography.fernet import InvalidToken
            except ImportError as exc:
                raise UserError("The cryptography package is required to mask CIN data.") from exc
            cipher, _digest_key = secure_record._cin_fernet()
            try:
                value = cipher.decrypt(secure_record.cin_encrypted.encode('ascii')).decode('utf-8')
            except (ValueError, UnicodeEncodeError, InvalidToken) as exc:
                raise UserError("Stored CIN data could not be decrypted; access is blocked.") from exc
            record.cin_masked = "****" + value[-4:]

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for values in vals_list:
            values = dict(values)
            cin = values.pop('cin', None)
            if 'cin_encrypted' in values or 'cin_digest' in values:
                raise UserError("Encrypted CIN fields are managed by the privacy service.")
            if cin is None:
                raise ValidationError("A CIN value is required.")
            values['cin_encrypted'], values['cin_digest'] = self._encrypt_cin(cin)
            prepared.append(values)
        return super().create(prepared)

    def write(self, vals):
        values = dict(vals)
        if {'cin_encrypted', 'cin_digest'}.intersection(values):
            raise UserError("Encrypted CIN fields are managed by the privacy service.")
        if 'cin' in values:
            cin = values.pop('cin')
            values['cin_encrypted'], values['cin_digest'] = self._encrypt_cin(cin)
        return super().write(values)
