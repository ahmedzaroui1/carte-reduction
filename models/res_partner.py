import pandas as pd

import base64
import calendar
import hashlib
import hmac
import html
import io
import secrets
from datetime import datetime, timedelta

import qrcode
import psycopg2

from odoo import fields, models, api, tools
from odoo.exceptions import AccessError, UserError, ValidationError
import logging
import os
import joblib

from odoo.tools import config

from .security_utils import (
    cin_blind_index,
    decrypt_cin,
    encrypt_cin,
    get_hmac_secret,
    hash_pin,
    log_security_event,
    mask_cin,
    reject_suspicious_input,
    read_ml_model_manifest,
    sha256_file,
    verify_pin,
    write_ml_model_manifest,
)

_logger = logging.getLogger(__name__)

POINTS_PROGRAM_NAME = "Cartes de Réduction - Points TND"
CARD_REDEMPTION_COOLDOWN_SECONDS = 60
CARD_TIER_RANK = {
    "bronze": 0,
    "standard": 1,
    "silver": 2,
    "gold": 3,
    "premium": 4,
    "platinum": 5,
}


class ResPartner(models.Model):
    _name = "res.partner"
    _inherit = ['res.partner', 'cartes.reduction.cin.mixin']

    # Relations existantes
    cartes_attribution_ids = fields.One2many(
        'cartes.attribution',
        'client_id',
        string="Cartes attribuées"
    )

    carte_ids = fields.One2many(
        'cartes.attribution',
        'client_id',
        string="Cartes attribuées"
    )

    pos_order_ids = fields.One2many(
        'pos.order',
        'partner_id',
        string="Commandes PoS"
    )

    total_sales = fields.Monetary(
        string="Total des Achats",
        compute="_compute_total_sales",
        store=True,
        currency_field="currency_id"
    )

    is_vip = fields.Boolean(
        string="Client VIP",
        compute="_compute_vip_status",
        store=True
    )

    currency_id = fields.Many2one(
        'res.currency',
        default=lambda self: self.env.company.currency_id
    )
    pos_otp_digest = fields.Char(copy=False, readonly=True, groups="base.group_system")
    pos_otp_qr_digest = fields.Char(copy=False, readonly=True, groups="base.group_system")
    pos_otp_consumed_digest = fields.Char(
        copy=False, readonly=True, groups="base.group_system"
    )
    pos_otp_nonce_digest = fields.Char(copy=False, readonly=True, groups="base.group_system")
    pos_otp_expires_at = fields.Datetime(copy=False, readonly=True, groups="base.group_system")
    pos_otp_order_uid = fields.Char(copy=False, readonly=True, groups="base.group_system")
    pos_otp_card_id = fields.Many2one(
        "cartes.reduction", copy=False, readonly=True, groups="base.group_system"
    )
    pos_otp_attempts = fields.Integer(
        default=0, copy=False, readonly=True, group_operator=None,
        groups="base.group_system",
    )
    pos_otp_locked_until = fields.Datetime(
        copy=False, readonly=True, groups="base.group_system"
    )
    pin_hash = fields.Char(
        copy=False, readonly=True, groups="base.group_system"
    )
    pos_verified_order_uid = fields.Char(
        copy=False, readonly=True, groups="base.group_system"
    )
    pos_verified_card_id = fields.Many2one(
        "cartes.reduction", copy=False, readonly=True, groups="base.group_system"
    )
    pos_verified_until = fields.Datetime(
        copy=False, readonly=True, groups="base.group_system"
    )
    last_card_redemption_at = fields.Datetime(
        copy=False, readonly=True, groups="base.group_system"
    )
    last_redeemed_card_id = fields.Many2one(
        "cartes.reduction", copy=False, readonly=True, groups="base.group_system"
    )
    last_card_redemption_order = fields.Char(
        copy=False, readonly=True, groups="base.group_system"
    )

    color = fields.Integer(group_operator=None)
    partner_gid = fields.Integer(group_operator=None)
    partner_latitude = fields.Float(digits=(10, 7), group_operator=None)
    partner_longitude = fields.Float(digits=(10, 7), group_operator=None)
    customer_rank = fields.Integer(group_operator=None)
    supplier_rank = fields.Integer(group_operator=None)
    message_bounce = fields.Integer(group_operator=None)
    debit_limit = fields.Monetary(
        currency_field="currency_id", group_operator=None
    )

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get("skip_card_eval"):
            return super().create(vals_list)

        tunisian_defaults = {
            "country_id": (
                self.env.ref("base.tn", raise_if_not_found=False).id
                if self.env.ref("base.tn", raise_if_not_found=False)
                else False
            ),
            "city": "Tunis",
            "street": "1 Avenue Habib Bourguiba",
            "phone": "+21600000000",
            "zip": "1000",
        }
        normalized_vals = []
        for vals in vals_list:
            vals = dict(vals)
            if not vals.get("is_company"):
                for field_name, default in tunisian_defaults.items():
                    if not vals.get(field_name):
                        vals[field_name] = default() if callable(default) else default
            normalized_vals.append(vals)

        partners = super().create(normalized_vals)
        for partner in partners:
            if partner.is_company and not partner.customer_rank:
                continue
            partner._assign_welcome_bronze()
        return partners

    def _get_points_program(self):
        self.ensure_one()
        program = self.env["loyalty.program"].search([
            ("name", "=", POINTS_PROGRAM_NAME),
            ("program_type", "=", "loyalty"),
            ("active", "=", True),
            ("company_id", "in", [False, self.env.company.id]),
        ], limit=1)
        if not program:
            raise UserError("Le programme de points TND n'est pas configuré.")
        return program

    def _get_or_create_points_card(self):
        self.ensure_one()
        partner = self.commercial_partner_id
        program = self._get_points_program()
        card = self.env["loyalty.card"].search([
            ("partner_id", "=", partner.id),
            ("program_id", "=", program.id),
            "|",
            ("expiration_date", "=", False),
            ("expiration_date", ">=", fields.Date.context_today(self)),
        ], limit=1)
        if not card:
            card = self.env["loyalty.card"].with_context(skip_card_eval=True).create({
                "partner_id": partner.id,
                "program_id": program.id,
                "points": 0.0,
            })
        return card

    def _get_active_tier_card(self):
        self.ensure_one()
        partner = self.commercial_partner_id
        attributions = self.env["cartes.attribution"].sudo().search([
            ("client_id", "child_of", partner.id),
            ("state", "in", ["validated", "renouvelé"]),
            "|",
            ("date_expiration", "=", False),
            ("date_expiration", ">=", fields.Date.context_today(self)),
        ])
        cards = attributions.mapped("carte_ids").filtered(
            lambda card: card.active and card.carte_type in CARD_TIER_RANK
        )
        return cards.sorted(
            key=lambda card: (CARD_TIER_RANK[card.carte_type], card.id)
        )[-1:]

    def _get_active_tier_card_reference(self, card):
        self.ensure_one()
        if not card:
            return ""
        attribution = self.commercial_partner_id.sudo()._active_tier_attributions().filtered(
            lambda record: card in record.carte_ids
        )[:1]
        return attribution.reference if attribution else (card.code or "")

    def _get_loyalty_points_balance(self):
        self.ensure_one()
        partner = self.commercial_partner_id
        programs = self.env["loyalty.program"].sudo().search([
            ("name", "=", POINTS_PROGRAM_NAME),
            ("program_type", "=", "loyalty"),
            ("active", "=", True),
            ("company_id", "in", [False, self.env.company.id]),
        ])
        if not programs:
            return 0.0
        cards = self.env["loyalty.card"].sudo().search([
            ("partner_id", "child_of", partner.id),
            ("program_id", "in", programs.ids),
            "|",
            ("expiration_date", "=", False),
            ("expiration_date", ">=", fields.Date.context_today(self)),
        ])
        return sum(cards.mapped("points"))

    def _render_loyalty_email(
        self,
        title,
        message,
        card=None,
        points_earned=0.0,
        points_balance=None,
        otp_code=None,
        qr_data_uri=None,
    ):
        self.ensure_one()
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        if not base_url:
            raise UserError(
                "Configurez l'URL publique du site avant l'envoi des e-mails."
            )
        base_url = base_url.rstrip("/")
        company = self.env.company
        brand_name = html.escape(company.name or "Sotufab Distribution Tunisie")
        logo_url = html.escape(
            f"{base_url}/logo.png?company={company.id}",
            quote=True,
        )
        if not card or getattr(card, "_name", False) != "cartes.reduction":
            card = self._get_active_tier_card()
        tier_labels = {
            "bronze": ("Bronze", "card-bronze-2.png"),
            "silver": ("Silver", "card-silver-5.png"),
            "gold": ("Gold VIP", "card-gold-vip-10.png"),
            "platinum": ("Platinum", "card-platinum-16.png"),
        }
        tier = tier_labels.get(card.carte_type) if card else None
        card_reference = self._get_active_tier_card_reference(card)
        points_balance = (
            self._get_loyalty_points_balance()
            if points_balance is None
            else points_balance
        )
        account_link = html.escape(f"{base_url}/my/cartes", quote=True)
        card_section = ""
        if tier:
            tier_name, image_name = tier
            cardholder_name = html.escape(self.name or "")
            safe_reference = html.escape(card_reference, quote=True)
            image_url = html.escape(
                f"{base_url}/cartes_reduction/static/img/cards/{image_name}",
                quote=True,
            )
            card_section = (
                '<tr><td style="padding:0 32px 0">'
                f'<img src="{image_url}" alt="{html.escape(tier_name, quote=True)}" '
                'width="536" style="display:block;width:100%;max-width:536px;'
                'height:auto;border-radius:12px 12px 0 0"/>'
                "</td></tr>"
                '<tr><td style="padding:0 32px 20px">'
                '<div style="padding:14px 18px;background:#172b4d;color:#fff;'
                'border-radius:0 0 12px 12px">'
                '<div style="font-size:11px;letter-spacing:1px;text-transform:uppercase;'
                'opacity:.8">Titulaire</div>'
                f'<strong style="font-size:16px">{cardholder_name}</strong>'
                + (
                    '<div style="margin-top:5px;font-size:12px;color:#dce4ef">'
                    f"Référence : {safe_reference}</div>"
                    if safe_reference
                    else ""
                )
                + "</div>"
                "</td></tr>"
            )
        points_section = (
            '<div style="display:inline-block;margin:4px 8px 4px 0;padding:10px 14px;'
            'background:#f3f5f7;border-radius:8px;color:#243244">'
            f'<strong>{points_balance:.2f}</strong> points disponibles</div>'
        )
        if points_earned:
            points_section += (
                '<div style="display:inline-block;margin:4px 0;padding:10px 14px;'
                'background:#e8f5ee;border-radius:8px;color:#176b3a">'
                f'<strong>+{points_earned:.2f}</strong> points gagnés</div>'
            )
        otp_section = ""
        if otp_code:
            otp_section = (
                '<div style="margin:22px 0;padding:20px;text-align:center;'
                'background:#f2f5fa;border:1px solid #dfe5ec;border-radius:12px">'
                '<div style="font-size:13px;color:#536273">VOTRE CODE POS</div>'
                f'<div style="margin-top:8px;font-size:34px;font-weight:700;'
                f'letter-spacing:8px;color:#172b4d">{html.escape(otp_code)}</div>'
                '<div style="margin-top:8px;font-size:13px;color:#536273">'
                "Valable 60 secondes et utilisable une seule fois.</div></div>"
            )
        qr_section = ""
        if qr_data_uri and qr_data_uri.startswith("data:image/png;base64,"):
            qr_section = (
                '<p style="text-align:center"><img alt="QR code POS" '
                f'src="{html.escape(qr_data_uri, quote=True)}" '
                'width="160" height="160" style="width:160px;height:160px"/></p>'
            )
        tier_badge = ""
        if tier:
            tier_badge = (
                '<span style="display:inline-block;margin:0 0 16px;padding:6px 12px;'
                'border-radius:20px;background:#edf1f6;color:#243244;'
                'font-size:13px;font-weight:700">'
                f"{html.escape(tier[0])} · {card.discount:g}%</span>"
            )
        safe_title = html.escape(title or "")
        safe_message = html.escape(message or "").replace("\n", "<br/>")
        return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<style>@media only screen and (max-width:600px){{.mail-card{{width:100%!important}}.mail-pad{{padding-left:20px!important;padding-right:20px!important}}}}</style>
</head><body style="margin:0;padding:24px 12px;background:#f3f5f7;font-family:Arial,Helvetica,sans-serif;color:#243244">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse"><tr><td align="center">
<table role="presentation" class="mail-card" width="600" cellspacing="0" cellpadding="0" style="width:100%;max-width:600px;background:#fff;border-radius:16px;overflow:hidden">
<tr><td class="mail-pad" style="padding:24px 32px;background:#0F172A;color:#fff">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>
<td><div style="font-size:12px;letter-spacing:2px;text-transform:uppercase;opacity:.8">Programme fidélité</div>
<div style="margin-top:6px;font-size:20px;font-weight:700">{brand_name}</div></td>
<td align="right"><img src="{logo_url}" alt="{brand_name}" width="150"
style="display:block;width:150px;max-width:100%;height:auto"/></td>
</tr></table></td></tr>
<tr><td class="mail-pad" style="padding:28px 32px 12px">
{tier_badge}<h1 style="margin:0 0 14px;font-size:24px;line-height:1.3;color:#172b4d">{safe_title}</h1>
<p style="margin:0 0 18px;font-size:16px;line-height:1.6">{safe_message}</p>
{points_section}{otp_section}{qr_section}
</td></tr>
{card_section}
<tr><td class="mail-pad" style="padding:8px 32px 30px">
<a href="{account_link}" style="display:inline-block;padding:13px 20px;border-radius:8px;background:#3568b8;color:#fff;text-decoration:none;font-weight:700">Voir mes cartes et mes points</a>
<p style="margin:22px 0 0;font-size:12px;line-height:1.5;color:#687789">Vous recevez cet e-mail au sujet de votre compte fidélité. Pour toute question, contactez notre équipe.</p>
</td></tr></table></td></tr></table></body></html>"""

    def _send_card_lifecycle_notification(
        self,
        card,
        notification_type,
        subject,
        message,
        *,
        points_earned=0.0,
        points_balance=None,
        otp_code=None,
        qr_data_uri=None,
    ):
        self.ensure_one()
        if self.email:
            email_from = (
                self.env.user.email_formatted
                or self.env.company.email_formatted
                or self.env.company.partner_id.email_formatted
            )
            if not email_from:
                raise UserError(
                    "Aucune adresse d'expédition n'est configurée pour l'entreprise."
                )
            self.env["mail.mail"].sudo().with_context(skip_card_eval=True).create({
                "subject": subject,
                "body_html": self._render_loyalty_email(
                    subject,
                    message,
                    card=card,
                    points_earned=points_earned,
                    points_balance=points_balance,
                    otp_code=otp_code,
                    qr_data_uri=qr_data_uri,
                ),
                "email_to": self.email,
                "email_from": email_from,
            }).send()

        return self.env["notifications.client"].sudo().with_context(
            skip_card_eval=True
        ).create({
            "name": subject,
            "message": message,
            "client_id": self.id,
            "date": fields.Datetime.now(),
            "notification_type": notification_type,
        })

    def _send_loyalty_summary(
        self,
        points_earned,
        message,
        upgraded_card=None,
        points_balance=None,
    ):
        self.ensure_one()
        partner = self if self.email else self.commercial_partner_id
        balance = (
            partner._get_loyalty_points_balance()
            if points_balance is None
            else points_balance
        )
        if upgraded_card:
            subject = f"Félicitations, votre carte évolue vers {upgraded_card.name}"
            message = (
                f"{message} Votre carte active est désormais {upgraded_card.name} "
                f"({upgraded_card.discount:g} %)."
            ).strip()
            notification_type = "card_upgrade"
            card = upgraded_card
        else:
            subject = f"{points_earned:.2f} points fidélité crédités"
            notification_type = "points_awarded"
            card = partner._get_active_tier_card()
        if points_earned:
            message = f"{message} Votre nouveau solde est de {balance:.2f} points.".strip()
        return partner._send_card_lifecycle_notification(
            card,
            notification_type,
            subject,
            message,
            points_earned=points_earned,
            points_balance=balance,
        )

    def _get_pos_otp_secret(self):
        return get_hmac_secret()

    def _pos_otp_digest(self, value, purpose):
        self.ensure_one()
        message = f"{purpose}:{self.id}:{value}".encode()
        return hmac.new(self._get_pos_otp_secret(), message, hashlib.sha256).hexdigest()

    def _try_lock_security_row(self):
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                self.env.cr.execute(
                    "SELECT id FROM res_partner WHERE id = %s FOR UPDATE NOWAIT",
                    (self.id,),
                )
                found = self.env.cr.fetchone()
        except psycopg2.errors.LockNotAvailable:
            log_security_event(
                self.env,
                "double_spend_blocked",
                partner_id=self.id,
                details="security row is locked by a concurrent operation",
            )
            return False
        return bool(found)

    def _record_security_failure(self, event_type, details):
        self.ensure_one()
        partner = self.sudo()
        now = fields.Datetime.now()
        attempts = partner.pos_otp_attempts + 1
        values = {"pos_otp_attempts": attempts}
        if attempts >= 3:
            values["pos_otp_locked_until"] = now + timedelta(minutes=5)
        partner.with_context(skip_card_eval=True).write(values)
        log_security_event(
            self.env,
            event_type,
            partner_id=partner.id,
            details=details,
        )

    def _security_admin_or_owner(self):
        return (
            self.env.su
            or self.env.user.has_group("base.group_system")
            or self.env.user.has_group("cartes_reduction.cartes_manager_group")
            or self.env.user.partner_id.commercial_partner_id == self.commercial_partner_id
        )

    def set_personal_security_pin(self, pin, current_pin=None):
        self.ensure_one()
        if not self._security_admin_or_owner():
            raise AccessError("Only the customer or a security administrator may set this PIN.")
        partner = self.commercial_partner_id.sudo()
        if not partner._try_lock_security_row():
            raise UserError("The customer security record is busy. Please retry.")
        partner.invalidate_recordset([
            "pin_hash", "pos_otp_attempts", "pos_otp_locked_until",
        ])
        is_admin = (
            self.env.su
            or self.env.user.has_group("base.group_system")
            or self.env.user.has_group("cartes_reduction.cartes_manager_group")
        )
        if partner.pin_hash and not is_admin and not verify_pin(
            partner.pin_hash, current_pin
        ):
            partner._record_security_failure("pin_failed", "incorrect current PIN")
            return False
        try:
            encoded_pin = hash_pin(pin)
        except ValueError as error:
            raise ValidationError(str(error)) from error
        partner.with_context(skip_card_eval=True).write({
            "pin_hash": encoded_pin,
            "pos_otp_attempts": 0,
            "pos_otp_locked_until": False,
        })
        log_security_event(
            self.env,
            "pin_set",
            partner_id=partner.id,
            details="customer PIN created or changed",
        )
        return True

    def verify_personal_security_pin(self, pin):
        self.ensure_one()
        if not (
            self.env.user.has_group("point_of_sale.group_pos_user")
            or self.env.user.has_group("base.group_system")
            or self.env.user.has_group("cartes_reduction.cartes_manager_group")
        ):
            raise AccessError("Only POS cashiers or security administrators may verify a PIN.")
        partner = self.commercial_partner_id.sudo()
        if not partner._try_lock_security_row():
            return False
        partner.invalidate_recordset([
            "pin_hash", "pos_otp_attempts", "pos_otp_locked_until",
        ])
        now = fields.Datetime.now()
        if partner.pos_otp_locked_until and partner.pos_otp_locked_until > now:
            return False
        if not partner.pin_hash:
            return True
        if not verify_pin(partner.pin_hash, pin):
            partner._record_security_failure("pin_failed", "incorrect PIN")
            return False
        partner.with_context(skip_card_eval=True).write({
            "pos_otp_attempts": 0,
            "pos_otp_locked_until": False,
        })
        return True

    def _issue_pos_otp(self, order_uid=None, card_id=None):
        self.ensure_one()
        partner = self.commercial_partner_id.sudo()
        if not partner._try_lock_security_row():
            raise UserError("The customer security record is busy. Please retry.")
        partner.invalidate_recordset([
            "pos_otp_locked_until", "pos_otp_attempts", "pin_hash",
        ])
        now = fields.Datetime.now()
        if partner.pos_otp_locked_until and partner.pos_otp_locked_until > now:
            raise UserError("Trop de tentatives. Réessayez après la fin du délai de sécurité.")
        if not partner.email:
            raise UserError("Le client doit avoir une adresse e-mail pour recevoir son code.")

        active_card = partner._get_active_tier_card()
        if card_id:
            try:
                card_id = int(card_id)
            except (TypeError, ValueError) as error:
                raise ValidationError("Invalid card identifier.") from error
            if not active_card or active_card.id != card_id:
                raise UserError("The selected discount card is not the customer's active tier.")
        elif order_uid and active_card:
            card_id = active_card.id

        code = f"{secrets.randbelow(1_000_000):06d}"
        expires_at = now + timedelta(seconds=60)
        expires_epoch = calendar.timegm(expires_at.timetuple())
        nonce = secrets.token_urlsafe(18)
        payload = f"CR1.{partner.id}.{expires_epoch}.{nonce}"
        signature = hmac.new(
            self._get_pos_otp_secret(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        qr_token = f"{payload}.{signature}"

        qr_image = qrcode.make(qr_token)
        image_buffer = io.BytesIO()
        qr_image.save(image_buffer, format="PNG")
        qr_data_uri = "data:image/png;base64," + base64.b64encode(
            image_buffer.getvalue()
        ).decode()

        partner.with_context(skip_card_eval=True).sudo().write({
            "pos_otp_digest": partner._pos_otp_digest(code, "otp"),
            "pos_otp_qr_digest": partner._pos_otp_digest(qr_token, "qr"),
            "pos_otp_nonce_digest": partner._pos_otp_digest(nonce, "nonce"),
            "pos_otp_expires_at": expires_at,
            "pos_otp_attempts": 0,
            "pos_otp_locked_until": False,
            "pos_otp_order_uid": order_uid or False,
            "pos_otp_card_id": card_id or False,
            "pos_verified_order_uid": False,
            "pos_verified_card_id": False,
            "pos_verified_until": False,
        })
        partner._send_card_lifecycle_notification(
            partner._get_active_tier_card(),
            "pos_otp",
            "Votre code de vérification POS",
            "Présentez ce code à la caisse pour confirmer votre carte de fidélité.",
            otp_code=code,
            qr_data_uri=qr_data_uri,
        )
        return {
            "code": code,
            "qr_token": qr_token,
            "qr_data_uri": qr_data_uri,
            "expires_epoch": expires_epoch,
            "expires_at": fields.Datetime.to_string(expires_at),
        }

    def action_send_pos_otp(self, order_uid=None, card_id=None):
        if not self.env.user.has_group("point_of_sale.group_pos_user"):
            raise AccessError("Seuls les utilisateurs du Point de Vente peuvent envoyer un code.")
        self.ensure_one()
        partner = self.commercial_partner_id.sudo()
        issued = partner._issue_pos_otp(order_uid=order_uid, card_id=card_id)
        return {
            "expires_epoch": issued["expires_epoch"],
            "message": "Le code de vérification a été envoyé au client.",
        }

    def verify_pos_otp_or_qr(self, code_or_qr, order_uid=None, card_id=None):
        if not self.env.user.has_group("point_of_sale.group_pos_user"):
            raise AccessError("Seuls les utilisateurs du Point de Vente peuvent vérifier un code.")
        self.ensure_one()
        if reject_suspicious_input(
            self.env,
            "/web/dataset/call_kw/res.partner/verify_pos_otp_or_qr",
            {"code_or_qr": code_or_qr, "order_uid": order_uid, "card_id": card_id},
        ):
            return {"success": False, "message": "Entrée suspecte rejetée."}
        partner = self.commercial_partner_id.sudo()
        if not partner._try_lock_security_row():
            return {
                "success": False,
                "message": "Une autre vérification est en cours. Réessayez.",
            }
        partner.invalidate_recordset([
            "pos_otp_digest", "pos_otp_qr_digest", "pos_otp_nonce_digest",
            "pos_otp_consumed_digest", "pos_otp_expires_at",
            "pos_otp_attempts", "pos_otp_locked_until",
            "pos_otp_order_uid", "pos_otp_card_id",
        ])
        now = fields.Datetime.now()

        if partner.pos_otp_locked_until and partner.pos_otp_locked_until > now:
            return {"success": False, "message": "Vérification temporairement verrouillée."}
        candidate = str(code_or_qr or "").strip()
        candidate_consumed_digest = partner._pos_otp_digest(candidate, "consumed")
        if not partner.pos_otp_expires_at:
            if (
                partner.pos_otp_consumed_digest
                and hmac.compare_digest(
                    partner.pos_otp_consumed_digest,
                    candidate_consumed_digest,
                )
            ):
                partner._record_security_failure(
                    "replay_blocked",
                    "Consumed POS OTP/QR replay attempt",
                )
            else:
                log_security_event(
                    self.env,
                    "otp_expired",
                    partner_id=partner.id,
                    details="POS OTP is not active",
                )
            return {"success": False, "message": "Le code est expiré. Demandez-en un nouveau."}
        if partner.pos_otp_expires_at <= now:
            log_security_event(
                self.env,
                "otp_expired",
                partner_id=partner.id,
                details="POS OTP expired",
            )
            return {"success": False, "message": "Le code est expiré. Demandez-en un nouveau."}
        if partner.pos_otp_attempts >= 3:
            return {"success": False, "message": "Trop de tentatives. Demandez un nouveau code plus tard."}

        is_qr = candidate.startswith("CR1.")
        expected = partner.pos_otp_qr_digest if is_qr else partner.pos_otp_digest
        actual = partner._pos_otp_digest(candidate, "qr" if is_qr else "otp")
        matches = hmac.compare_digest(expected or ("0" * 64), actual)
        if is_qr:
            try:
                version, partner_id, expiry, nonce, signature = candidate.split(".")
                payload = ".".join((version, partner_id, expiry, nonce))
                expected_signature = hmac.new(
                    partner._get_pos_otp_secret(),
                    payload.encode(),
                    hashlib.sha256,
                ).hexdigest()
                expected_nonce_digest = partner._pos_otp_digest(nonce, "nonce")
                signature_valid = hmac.compare_digest(signature, expected_signature)
                if not signature_valid:
                    log_security_event(
                        self.env,
                        "hmac_invalid",
                        partner_id=partner.id,
                        details="POS QR signature mismatch",
                    )
                matches = (
                    matches
                    and version == "CR1"
                    and int(partner_id) == partner.id
                    and int(expiry) >= calendar.timegm(now.timetuple())
                    and signature_valid
                    and hmac.compare_digest(
                        partner.pos_otp_nonce_digest or ("0" * 64),
                        expected_nonce_digest,
                    )
                )
            except (TypeError, ValueError):
                matches = False

        if partner.pos_otp_order_uid and partner.pos_otp_order_uid != order_uid:
            matches = False
        expected_card_id = partner.pos_otp_card_id.id or False
        try:
            requested_card_id = int(card_id) if card_id else False
        except (TypeError, ValueError):
            requested_card_id = False
            matches = False
        if expected_card_id and expected_card_id != requested_card_id:
            matches = False
        active_card = partner._get_active_tier_card()
        if requested_card_id and (
            not active_card or active_card.id != requested_card_id
        ):
            matches = False
        if active_card and order_uid and requested_card_id != active_card.id:
            matches = False

        if not matches:
            if (
                partner.pos_otp_consumed_digest
                and hmac.compare_digest(
                    partner.pos_otp_consumed_digest,
                    candidate_consumed_digest,
                )
            ):
                partner._record_security_failure(
                    "replay_blocked",
                    "Consumed POS OTP/QR replay attempt",
                )
            else:
                partner._record_security_failure(
                    "pos_otp_failed",
                    "OTP, QR, card, or order binding did not match",
                )
            return {"success": False, "message": "Code incorrect."}

        partner.with_context(skip_card_eval=True).write({
            "pos_otp_consumed_digest": candidate_consumed_digest,
            "pos_otp_digest": False,
            "pos_otp_qr_digest": False,
            "pos_otp_nonce_digest": partner._pos_otp_digest(
                secrets.token_urlsafe(18), "nonce"
            ),
            "pos_otp_expires_at": False,
            "pos_otp_order_uid": False,
            "pos_otp_card_id": False,
            "pos_otp_attempts": 0,
            "pos_otp_locked_until": False,
            "pos_verified_order_uid": order_uid or False,
            "pos_verified_card_id": requested_card_id or False,
            "pos_verified_until": (
                now + timedelta(seconds=60) if requested_card_id else False
            ),
        })
        return {"success": True, "message": "Client vérifié."}

    def _assign_welcome_bronze(self):
        if self.env.context.get("skip_card_eval"):
            return False
        self.ensure_one()
        partner = self.commercial_partner_id
        today = fields.Date.context_today(self)
        bronze = self.env["cartes.reduction"].search([
            ("carte_type", "=", "bronze"),
            ("active", "=", True),
        ], order="id", limit=1)
        if not bronze:
            raise UserError("Aucune carte Bronze active n'est configurée.")

        existing = partner._normalize_active_tier_attributions()
        if existing:
            return existing

        points_card = partner._get_or_create_points_card()
        attribution = self.env["cartes.attribution"].with_context(skip_card_eval=True).create({
            "client_id": partner.id,
            "carte_ids": [(6, 0, bronze.ids)],
            "state": "validated",
            "date_attribution": bronze.date_debut or today,
            "date_expiration": bronze.date_expiration or (today + timedelta(days=365)),
        })
        bronze.with_context(skip_card_eval=True).write({
            "nombre_utilisateurs": bronze.nombre_utilisateurs + 1,
        })
        (self if self.email else partner)._send_card_lifecycle_notification(
            bronze,
            "welcome",
            f"Bienvenue - carte {bronze.name}",
            f"Votre carte Bronze est active. Votre solde initial est de {points_card.points:.2f} points.",
            points_balance=points_card.points,
        )
        return attribution

    def _active_tier_attributions(self):
        self.ensure_one()
        partner = self.commercial_partner_id
        attributions = self.env["cartes.attribution"].search([
            ("client_id", "child_of", partner.id),
            ("state", "in", ["validated", "renouvelé"]),
            ("date_expiration", ">=", fields.Date.context_today(self)),
        ])
        return attributions.filtered(
            lambda attribution: any(
                card.active and card.carte_type in CARD_TIER_RANK
                for card in attribution.carte_ids
            )
        )

    def _expire_tier_attribution(self, attribution):
        cards = attribution.carte_ids
        attribution.with_context(skip_card_eval=True).write({"state": "expired"})
        for card in cards:
            card.with_context(skip_card_eval=True).write({
                "nombre_utilisateurs": max(0, card.nombre_utilisateurs - 1),
            })

    def _normalize_active_tier_attributions(self):
        self.ensure_one()
        partner = self.commercial_partner_id
        ranked_attributions = []
        for attribution in partner._active_tier_attributions():
            tier_cards = attribution.carte_ids.filtered(
                lambda card: card.active and card.carte_type in CARD_TIER_RANK
            )
            if tier_cards:
                highest_rank = max(CARD_TIER_RANK[card.carte_type] for card in tier_cards)
                ranked_attributions.append((attribution, highest_rank, tier_cards))
        if not ranked_attributions:
            return self.env["cartes.attribution"]

        highest_rank = max(rank for _attribution, rank, _cards in ranked_attributions)
        highest_attributions = [
            item for item in ranked_attributions if item[1] == highest_rank
        ]
        keeper = min(
            highest_attributions,
            key=lambda item: (item[0].client_id != partner, item[0].id),
        )
        attribution, _rank, tier_cards = keeper
        keep_card = tier_cards.sorted("id")[:1]
        unrelated_cards = attribution.carte_ids.filtered(
            lambda card: card.carte_type not in CARD_TIER_RANK
        )
        kept_cards = keep_card | unrelated_cards
        cards_to_remove = attribution.carte_ids - kept_cards
        if cards_to_remove:
            attribution.with_context(skip_card_eval=True).write({
                "carte_ids": [(6, 0, kept_cards.ids)],
            })
            for card in cards_to_remove:
                card.with_context(skip_card_eval=True).write({
                    "nombre_utilisateurs": max(0, card.nombre_utilisateurs - 1),
                })

        for other_attribution in partner._active_tier_attributions() - attribution:
            self._expire_tier_attribution(other_attribution)
        return attribution

    def _evaluate_next_card_tier(self, notify=True):
        if self.env.context.get("skip_card_eval"):
            return False
        self.ensure_one()
        partner = self.commercial_partner_id
        today = fields.Date.context_today(self)
        tier_rank = {"bronze": 0, "silver": 1, "gold": 2, "platinum": 3}
        attributions = partner._normalize_active_tier_attributions()
        active_cards = attributions.mapped("carte_ids").filtered(
            lambda card: card.active and card.carte_type in tier_rank
        )
        if not active_cards:
            partner._assign_welcome_bronze()
            return False

        assigned_attributions = attributions
        last_attribution = False
        while True:
            current_rank = max(
                tier_rank[card.carte_type]
                for card in assigned_attributions.mapped("carte_ids").filtered(
                    lambda card: card.active and card.carte_type in tier_rank
                )
            )
            next_rank = current_rank + 1
            next_type = next(
                (name for name, rank in tier_rank.items() if rank == next_rank),
                False,
            )
            if not next_type:
                break

            next_card = self.env["cartes.reduction"].search([
                ("carte_type", "=", next_type),
                ("active", "=", True),
            ], order="id", limit=1)
            if not next_card or not next_card.is_eligible_for(partner):
                break
            if self.env["cartes.attribution"].search_count([
                ("client_id", "child_of", partner.id),
                ("carte_ids", "in", next_card.ids),
                ("state", "in", ["validated", "renouvelé"]),
                ("date_expiration", ">=", today),
            ]):
                break

            previous_attributions = assigned_attributions.filtered(
                lambda attribution: any(
                    tier_rank.get(card.carte_type, next_rank) < next_rank
                    for card in attribution.carte_ids
                )
            )
            for attribution in previous_attributions:
                partner._expire_tier_attribution(attribution)

            last_attribution = self.env["cartes.attribution"].with_context(
                skip_card_eval=True
            ).create({
                "client_id": partner.id,
                "carte_ids": [(6, 0, next_card.ids)],
                "state": "validated",
                "date_attribution": next_card.date_debut or today,
                "date_expiration": next_card.date_expiration or (
                    today + timedelta(days=365)
                ),
            })
            next_card.with_context(skip_card_eval=True).write({
                "nombre_utilisateurs": next_card.nombre_utilisateurs + 1,
            })
            if notify and next_card.is_vip:
                partner._send_vip_notification(
                    partner, next_card, create_attribution=False
                )
            elif notify:
                partner._send_card_lifecycle_notification(
                    next_card,
                    "card_upgrade",
                    f"Votre carte évolue vers {next_card.name}",
                    f"Félicitations, votre carte a évolué vers "
                    f"{next_card.name} ({next_card.discount:g} %).",
                )
            assigned_attributions = last_attribution
        return last_attribution

    def _security_velocity_bypassed(self):
        return bool(
            config["test_enable"]
            and not self.env.context.get("enforce_security_checks")
        )

    def _order_card_redemption(self, order):
        self.ensure_one()
        if order._name == "sale.order":
            return order.applied_discount_card_id
        if order._name == "pos.order":
            cards = order.lines.filtered(
                lambda line: line.discount > 0 and line.custom_discount_card_id
            ).mapped("custom_discount_card_id")
            if len(cards) > 1:
                raise UserError("A POS order cannot redeem multiple discount cards.")
            return cards[:1]
        return self.env["cartes.reduction"]

    def _record_card_redemption(self, order, card):
        self.ensure_one()
        if not card:
            return
        partner = self.commercial_partner_id.sudo()
        if not partner._try_lock_security_row():
            raise UserError("The customer security record is busy. Please retry.")
        partner.invalidate_recordset([
            "last_card_redemption_at",
            "last_redeemed_card_id",
            "last_card_redemption_order",
        ])
        order_key = (
            order.pos_reference
            if order._name == "pos.order"
            else order.name
        ) or f"{order._name}:{order.id}"
        now = fields.Datetime.now()
        previous_at = partner.last_card_redemption_at
        if (
            previous_at
            and partner.last_redeemed_card_id == card
            and partner.last_card_redemption_order == order_key
        ):
            return
        if (
            not self._security_velocity_bypassed()
            and partner.last_redeemed_card_id == card
            and previous_at
            and previous_at > now - timedelta(
                seconds=CARD_REDEMPTION_COOLDOWN_SECONDS
            )
            and partner.last_card_redemption_order != order_key
        ):
            log_security_event(
                self.env,
                "velocity_abuse",
                partner_id=partner.id,
                details=(
                    f"card_id={card.id}; order={order_key}; "
                    f"cooldown_seconds={CARD_REDEMPTION_COOLDOWN_SECONDS}"
                ),
            )
            raise UserError(
                "VELOCITY_ABUSE: this card was redeemed too recently. "
                "Please wait before retrying."
            )

        partner.with_context(skip_card_eval=True).write({
            "last_card_redemption_at": now,
            "last_redeemed_card_id": card.id,
            "last_card_redemption_order": order_key,
        })
        if not self._security_velocity_bypassed():
            log_security_event(
                self.env,
                "redemption",
                partner_id=partner.id,
                details=f"card_id={card.id}; order={order_key}",
            )

    def _consume_verified_pos_redemption(self, order):
        self.ensure_one()
        partner = self.commercial_partner_id.sudo()
        discounted_lines = order.lines.filtered(
            lambda line: line.discount > 0 and not line.is_reward_line
        )
        tagged_cards = discounted_lines.mapped("custom_discount_card_id")
        active_card = partner._get_active_tier_card()
        if not tagged_cards:
            if active_card and discounted_lines:
                raise UserError(
                    "A card discount requires a valid OTP or signed QR verification."
                )
            return False
        if len(tagged_cards) != 1:
            raise UserError("A POS order cannot redeem multiple discount cards.")

        card = tagged_cards[0]
        if not partner._try_lock_security_row():
            raise UserError("The customer security record is busy. Please retry.")
        partner.invalidate_recordset([
            "pos_verified_order_uid",
            "pos_verified_card_id",
            "pos_verified_until",
            "last_card_redemption_at",
            "last_redeemed_card_id",
            "last_card_redemption_order",
        ])
        now = fields.Datetime.now()
        if (
            card != active_card
            or partner.pos_verified_order_uid != order.card_redemption_order_uid
            or partner.pos_verified_card_id != card
            or not partner.pos_verified_until
            or partner.pos_verified_until <= now
        ):
            raise UserError("The POS card verification is missing, expired, or for another order.")
        if any(round(line.discount, 2) != round(card.discount, 2) for line in discounted_lines):
            raise UserError("The POS card discount does not match the verified card.")

        self._record_card_redemption(order, card)
        partner.with_context(skip_card_eval=True).write({
            "pos_verified_order_uid": False,
            "pos_verified_card_id": False,
            "pos_verified_until": False,
        })
        return True

    def _process_card_purchase(self, order):
        if self.env.context.get("skip_card_eval"):
            return False
        self.ensure_one()
        if not order or order.loyalty_points_processed:
            return False
        security_partner = self.commercial_partner_id.sudo()
        if not security_partner._try_lock_security_row():
            raise UserError("The customer security record is busy. Please retry.")
        card = self._order_card_redemption(order)
        self._record_card_redemption(order, card)

        program = self._get_points_program()
        earning_rule = program.rule_ids.filtered(
            lambda rule: rule.active and rule.reward_point_mode == "money"
        )[:1]
        if not earning_rule:
            raise UserError("Le programme de points doit avoir une règle monétaire active.")

        order_currency = order.currency_id
        order_date = order.date_order.date() if order.date_order else fields.Date.context_today(self)
        amount_tnd = order_currency._convert(
            order.amount_total,
            program.currency_id,
            order.company_id or self.env.company,
            order_date,
        )
        points = max(0.0, amount_tnd * earning_rule.reward_point_amount)
        if amount_tnd < earning_rule.minimum_amount:
            points = 0.0

        points_card = self._get_or_create_points_card()
        if points:
            points_card.with_context(skip_card_eval=True).write({
                "points": points_card.points + points,
            })

        order.with_context(skip_card_eval=True).write({"loyalty_points_processed": True})
        self.invalidate_recordset(["total_sales", "is_vip"])
        self._compute_total_sales()
        self._compute_vip_status()
        upgraded_attribution = self._evaluate_next_card_tier(notify=False)
        self.with_context(skip_card_eval=True).update_behavior_analysis()
        if points or upgraded_attribution:
            upgraded_card = (
                upgraded_attribution.carte_ids[:1]
                if upgraded_attribution
                else False
            )
            mail_partner = self if self.email else self.commercial_partner_id
            message = (
                f"Après votre achat de {amount_tnd:.2f} TND, vous avez gagné "
                f"{points:.2f} points."
                if points
                else "Votre achat vous a permis d'atteindre les conditions d'évolution."
            )
            mail_partner._send_loyalty_summary(
                points,
                message,
                upgraded_card=upgraded_card,
                points_balance=points_card.points,
            )
        return points

    # Analyse comportementale
    avg_purchase_value = fields.Monetary(
        string="Moyenne des Achats",
        compute="_compute_purchase_stats",
        store=True,
        currency_field="currency_id",
        group_operator="avg",
    )

    purchase_order_count = fields.Integer(
        string="Nombre d'Achats",
        compute="_compute_purchase_stats",
        store=True,
        group_operator=None,
    )

    purchase_frequency = fields.Float(
        string="Fréquence d'Achat (jours)",
        compute="_compute_purchase_stats",
        store=True,
        group_operator=None,
    )

    last_purchase_date = fields.Date(
        string="Dernier Achat",
        compute="_compute_purchase_stats",
        store=True
    )

    customer_segment = fields.Selection([
        ('new', 'Nouveau Client'),
        ('regular', 'Client Régulier'),
        ('vip', 'Client VIP'),
        ('churned', 'À Risque')
    ], string="Segment Client", compute="_compute_customer_segment", store=True)

    segment_color = fields.Selection([
        ('success', 'Succès'),
        ('info', 'Information'),
        ('warning', 'Attention'),
        ('danger', 'Risque'),
    ], string="Couleur du segment", compute="_compute_segment_color", store=True)

    # Scoring RFM
    recency_score = fields.Integer(
        string="Score Récence",
        compute="_compute_rfm_scores",
        store=True,
        group_operator=None,
    )

    frequency_score = fields.Integer(
        string="Score Fréquence",
        compute="_compute_rfm_scores",
        store=True,
        group_operator=None,
    )

    monetary_score = fields.Integer(
        string="Score Montant",
        compute="_compute_rfm_scores",
        store=True,
        group_operator=None,
    )

    rfm_score = fields.Integer(
        string="Score RFM",
        compute="_compute_rfm_scores",
        store=True,
        group_operator=None,
    )

    # Prédictions
    predicted_clv = fields.Monetary(
        string="CLV Prédite 12 mois",
        compute="_predict_ml_metrics",
        store=True
    )

    churn_probability = fields.Float(
        string="Probabilité de Désertion",
        compute="_predict_ml_metrics",
        store=True,
        group_operator="avg",
    )

    next_purchase_prediction = fields.Date(
        string="Date Achat Prédite",
        compute="_predict_ml_metrics",
        store=True
    )

    # Méthodes de base
    @api.depends(
        'pos_order_ids.amount_total',
        'pos_order_ids.state',
        'pos_order_ids.date_order',
        'sale_order_ids.amount_total',
        'sale_order_ids.state',
        'sale_order_ids.date_order',
        'child_ids',
        'child_ids.pos_order_ids.amount_total',
        'child_ids.pos_order_ids.state',
        'child_ids.pos_order_ids.date_order',
        'child_ids.sale_order_ids.amount_total',
        'child_ids.sale_order_ids.state',
        'child_ids.sale_order_ids.date_order',
        'commercial_partner_id',
    )
    def _compute_total_sales(self):
        """Calcule le total des commandes confirmées, en devise du partenaire."""
        for partner in self:
            pos_orders, sale_orders = partner._get_confirmed_purchase_orders()
            target_currency = partner.currency_id or self.env.company.currency_id
            total = 0.0
            for order in list(pos_orders) + list(sale_orders):
                order_date = order.date_order.date() if order.date_order else fields.Date.context_today(partner)
                total += order.currency_id._convert(
                    order.amount_total,
                    target_currency,
                    order.company_id or self.env.company,
                    order_date,
                )
            partner.total_sales = total

    def _get_confirmed_purchase_orders(self):
        self.ensure_one()
        commercial_partner = self.commercial_partner_id
        pos_orders = self.env['pos.order'].search([
            ('partner_id', 'child_of', commercial_partner.id),
            ('state', 'in', ['paid', 'done', 'invoiced']),
        ])
        sale_orders = self.env['sale.order'].search([
            ('partner_id', 'child_of', commercial_partner.id),
            ('state', 'in', ['sale', 'done']),
        ])
        return pos_orders, sale_orders

    @api.depends('total_sales')
    def _compute_vip_status(self):
        """Définit un client comme VIP si son total des achats dépasse un seuil"""
        VIP_THRESHOLD = 200  # Seuil augmenté pour un vrai cas VIP

        for record in self:
            record.is_vip = record.total_sales >= VIP_THRESHOLD

    def _send_vip_notification(self, partner, card_program, create_attribution=True):
        """Envoie la notification VIP"""
        subject = f"Félicitations {partner.name} ! Vous êtes désormais VIP"
        message = (
            f"Votre carte VIP {card_program.name} est active "
            f"({card_program.discount:g} % de remise). Total des achats : "
            f"{partner.total_sales:.2f} {partner.currency_id.symbol or ''}."
        )
        partner._send_card_lifecycle_notification(
            card_program,
            "vip",
            subject,
            message,
        )

        if create_attribution:
            today = fields.Date.context_today(self)
            existing = self.env['cartes.attribution'].search([
                ('client_id', '=', partner.id),
                ('carte_ids', 'in', card_program.ids),
                ('state', 'in', ['validated', 'renouvelé']),
                ('date_expiration', '>=', today),
            ], limit=1)
            if not existing:
                self.env['cartes.attribution'].with_context(skip_card_eval=True).create({
                    'client_id': partner.id,
                    'carte_ids': [(6, 0, card_program.ids)],
                    'date_attribution': card_program.date_debut or today,
                    'date_expiration': card_program.date_expiration or (today + timedelta(days=15)),
                    'state': 'validated',
                })

    # Méthodes d'analyse comportementale
    @api.depends(
        'pos_order_ids',
        'pos_order_ids.amount_total',
        'pos_order_ids.date_order',
        'pos_order_ids.state',
        'sale_order_ids',
        'sale_order_ids.amount_total',
        'sale_order_ids.date_order',
        'sale_order_ids.state',
        'child_ids',
        'child_ids.pos_order_ids.amount_total',
        'child_ids.pos_order_ids.state',
        'child_ids.pos_order_ids.date_order',
        'child_ids.sale_order_ids.amount_total',
        'child_ids.sale_order_ids.state',
        'child_ids.sale_order_ids.date_order',
        'commercial_partner_id',
    )
    def _compute_purchase_stats(self):
        """Calcule les statistiques sur les commandes confirmées de la famille commerciale."""
        for partner in self:
            pos_orders, sale_orders = partner._get_confirmed_purchase_orders()
            pos_orders = pos_orders.filtered('date_order')
            sale_orders = sale_orders.filtered('date_order')
            orders = list(pos_orders) + list(sale_orders)

            # Calcul des métriques de base
            partner.purchase_order_count = len(orders)
            partner.avg_purchase_value = partner.total_sales / len(orders) if orders else 0

            # Calcul de la fréquence
            if len(orders) > 1:
                dates = sorted(order.date_order for order in orders)
                deltas = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
                partner.purchase_frequency = sum(deltas) / len(deltas) if deltas else 30
            else:
                partner.purchase_frequency = 30  # Valeur par défaut réaliste

            last_purchase = max(
                (order.date_order for order in orders),
                default=False,
            )
            partner.last_purchase_date = fields.Date.to_date(last_purchase) if last_purchase else False

    @api.depends('total_sales', 'last_purchase_date', 'purchase_frequency', 'is_vip')
    def _compute_rfm_scores(self):
        """Calcule le score RFM avec des seuils adaptés"""
        for partner in self:
            # Recency (1-5)
            recency = (fields.Date.today() - partner.last_purchase_date).days if partner.last_purchase_date else 365
            partner.recency_score = min(5, max(1, 6 - (recency // 30)))

            # Frequency (1-5)
            freq = partner.purchase_frequency or 30
            partner.frequency_score = min(5, max(1, int(30 / max(1, freq))))  # Évite division par zéro

            # Monetary (1-5) - Seuil ajusté pour les VIP
            monetary = partner.total_sales or 0
            base_amount = 200 if partner.is_vip else 200  # Seuil différent pour les VIP
            partner.monetary_score = min(5, max(1, int(monetary / base_amount)))

            # Score RFM global (100-500)
            partner.rfm_score = (partner.recency_score * 100 +
                                 partner.frequency_score * 10 +
                                 partner.monetary_score)

    @api.depends('rfm_score', 'last_purchase_date')
    def _compute_customer_segment(self):
        """Détermine le segment client avec des règles métiers claires"""
        for partner in self:
            if not partner.last_purchase_date:
                partner.customer_segment = 'new'
            elif (fields.Date.today() - partner.last_purchase_date).days > 90:
                partner.customer_segment = 'churned'
            elif partner.rfm_score >= 450:
                partner.customer_segment = 'vip'
            elif partner.rfm_score >= 300:
                partner.customer_segment = 'regular'
            else:
                partner.customer_segment = 'new'

    @api.depends('customer_segment')
    def _compute_segment_color(self):
        colors = {
            'vip': 'success',
            'regular': 'info',
            'new': 'warning',
            'churned': 'danger',
        }
        for partner in self:
            partner.segment_color = colors.get(partner.customer_segment, 'warning')

    # Méthodes de prédiction améliorées (sans ML obligatoire)
    @api.depends(
        'total_sales',
        'avg_purchase_value',
        'purchase_order_count',
        'purchase_frequency',
        'last_purchase_date',
        'is_vip',
        'customer_segment',
    )
    def _predict_metrics(self):
        """Nouvelle méthode unifiée de prédiction avec fallback"""
        for partner in self:
            try:
                # CLV is a 12-month revenue estimate adjusted for churn risk.
                partner.churn_probability = self._calculate_churn_prob(partner)
                partner.predicted_clv = self._calculate_clv(partner)
                partner.next_purchase_prediction = self._predict_next_purchase(partner)

            except Exception as e:
                _logger.error(f"Erreur prédiction partenaire {partner.id}: {str(e)}")
                partner.update({
                    'predicted_clv': 0,
                    'churn_probability': 0.5,
                    'next_purchase_prediction': False
                })

    def _calculate_clv(self, partner):
        """Estimate next-12-month revenue from observed order value and cadence."""
        if not partner.purchase_order_count:
            return 0.0

        annual_order_count = (
            1.0
            if partner.purchase_order_count == 1
            else 365.0 / max(partner.purchase_frequency or 365.0, 1.0)
        )
        churn_probability = min(max(partner.churn_probability or 0.0, 0.0), 1.0)
        return (
            (partner.avg_purchase_value or 0.0)
            * annual_order_count
            * (1.0 - churn_probability)
        )

    def _calculate_churn_prob(self, partner):
        """Probabilité basée sur l'activité récente"""
        if not partner.last_purchase_date:
            return 0.9  # 90% si jamais acheté

        inactive_days = (fields.Date.today() - partner.last_purchase_date).days
        freq = partner.purchase_frequency or 30

        # Formule de base
        prob = 0.3  # 30% de base
        prob += inactive_days * 0.005  # +0.5% par jour d'inactivité
        prob -= freq * 0.01  # -1% par jour de fréquence

        # Réduction pour les VIP
        if partner.is_vip:
            prob *= 0.6  # -40% pour les VIP

        return max(0.01, min(0.9, prob))  # Borné entre 1% et 90%

    def _predict_next_purchase(self, partner):
        """Prédiction hybride avec fallback simple"""
        if not partner.last_purchase_date:
            return False

        freq = partner.purchase_frequency or 30

        # Ajustement selon le segment
        segment_adjustment = {
            'vip': 0.7,  # Les VIP reviennent 30% plus vite
            'regular': 1.0,
            'new': 1.3,  # Nouveaux clients prennent plus de temps
            'churned': 2.0  # Clients inactifs reviennent rarement
        }

        adjusted_days = freq * segment_adjustment.get(partner.customer_segment, 1.0)
        return partner.last_purchase_date + timedelta(days=adjusted_days)

    def _get_ml_model(self):
        """Charge le modèle ML si disponible"""
        try:
            model_path = os.path.join(
                tools.config['data_dir'],
                'ml_models',
                'model.pkl'
            )
            if os.path.exists(model_path):
                expected = os.environ.get("CARTES_ML_MODEL_SHA256")
                if not expected:
                    expected = self.env["ir.config_parameter"].sudo().get_param(
                        "cartes_reduction.ml_model_sha256"
                    )
                if not expected or not isinstance(expected, str) or len(expected.strip()) != 64:
                    log_security_event(
                        self.env,
                        "ml_integrity_failure",
                        partner_id=self[:1].id,
                        details="trusted ML checksum unavailable",
                    )
                    return None
                expected = expected.strip().lower()
                if any(ch not in "0123456789abcdef" for ch in expected):
                    log_security_event(
                        self.env,
                        "ml_integrity_failure",
                        partner_id=self[:1].id,
                        details="invalid trusted ML checksum",
                    )
                    return None
                digest = sha256_file(model_path)
                if not hmac.compare_digest(digest, expected):
                    log_security_event(
                        self.env,
                        "ml_integrity_failure",
                        partner_id=self[:1].id,
                        details="ML artifact SHA-256 mismatch",
                    )
                    return None

                try:
                    manifest_digest = read_ml_model_manifest(model_path)
                except FileNotFoundError:
                    write_ml_model_manifest(
                        model_path,
                        digest,
                    )
                    manifest_digest = read_ml_model_manifest(model_path)
                if (
                    len(manifest_digest) != 64
                    or any(ch not in "0123456789abcdef" for ch in manifest_digest)
                    or not hmac.compare_digest(manifest_digest, digest)
                    or not hmac.compare_digest(manifest_digest, expected)
                ):
                    log_security_event(
                        self.env,
                        "ml_integrity_failure",
                        partner_id=self[:1].id,
                        details="ML artifact manifest mismatch",
                    )
                    return None
                return joblib.load(model_path)
            return None
        except Exception as e:
            _logger.error(f"Erreur chargement modèle ML: {str(e)}")
            return None

    @api.depends(
        'total_sales',
        'avg_purchase_value',
        'purchase_order_count',
        'purchase_frequency',
        'last_purchase_date',
        'is_vip',
        'customer_segment',
    )
    def _predict_ml_metrics(self):
        model = self._get_ml_model()
        if not model:
            return self._predict_metrics()

        try:
            partners_with_orders = self.filtered('purchase_order_count')
            partners_without_orders = self - partners_with_orders
            for partner in partners_without_orders:
                partner.predicted_clv = 0.0
                partner.churn_probability = 0.9
                partner.next_purchase_prediction = False

            if not partners_with_orders:
                return True

            prediction_data = []
            for partner in partners_with_orders:
                prediction_data.append({
                    'total_sales': partner.total_sales or 0,
                    'purchase_frequency': partner.purchase_frequency or 30,
                    'days_since_last_purchase': (
                                fields.Date.today() - partner.last_purchase_date).days if partner.last_purchase_date else 90,
                    'is_vip': int(partner.is_vip),
                    'customer_segment': {'new': 0, 'regular': 1, 'vip': 2, 'churned': 3}.get(partner.customer_segment,
                                                                                             0)
                })

            df = pd.DataFrame(prediction_data)
            required_features = ['total_sales', 'purchase_frequency', 'days_since_last_purchase', 'is_vip',
                                 'customer_segment']

            predictions = model.predict(df[required_features])

            for i, partner in enumerate(partners_with_orders):
                partner.churn_probability = float(predictions[i][1])
                partner.next_purchase_prediction = fields.Date.today() + timedelta(days=float(predictions[i][2]))
                partner.predicted_clv = self._calculate_clv(partner)
                _logger.info(f"Partenaire {partner} mis à jour avec les prédictions ML: "
                         f"predicted_clv={partner.predicted_clv}, "
                         f"churn_probability={partner.churn_probability}, "
                         f"next_purchase_prediction={partner.next_purchase_prediction}")
            _logger.info("modifier avec ml ")
            return True
        except Exception as e:
            _logger.error(f"Erreur prédiction ML: {str(e)}")
            return self._predict_metrics()


    def update_behavior_analysis(self):
        """Met à jour toutes les analyses"""
        self._compute_total_sales()
        self._compute_purchase_stats()
        self._compute_rfm_scores()
        self._compute_customer_segment()
        self._predict_ml_metrics()
        return True

    def action_refresh_ai_predictions(self):
        if not self.env.user.has_group('base.group_system'):
            raise AccessError("Seuls les administrateurs peuvent actualiser les analyses IA.")
        (self or self.env['res.partner'].search([])).sudo().update_behavior_analysis()
        return self.env['product.association.generator'].sudo().generate_association_rules()



    def action_test_cron(self):
        # Crée une instance de CustomerMLCron
        cron_model = self.env['customer.ml.cron']
        cron_model.action_test_cron()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Succès',
                'message': f'{cron_model.processed_partners} clients mis à jour',
                'sticky': False,
            }
        }
