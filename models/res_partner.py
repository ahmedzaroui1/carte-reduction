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

from odoo import fields, models, api, tools
from odoo.exceptions import AccessError, UserError
import logging
import os
import joblib

from odoo.tools import config

_logger = logging.getLogger(__name__)

POINTS_PROGRAM_NAME = "Cartes de Réduction - Points TND"
CARD_TIER_RANK = {
    "bronze": 0,
    "standard": 1,
    "silver": 2,
    "gold": 3,
    "premium": 4,
}


class ResPartner(models.Model):
    _inherit = 'res.partner'

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
    pos_otp_digest = fields.Char(copy=False, readonly=True)
    pos_otp_qr_digest = fields.Char(copy=False, readonly=True)
    pos_otp_expires_at = fields.Datetime(copy=False, readonly=True)
    pos_otp_attempts = fields.Integer(default=0, copy=False, readonly=True)
    pos_otp_locked_until = fields.Datetime(copy=False, readonly=True)

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

    def _send_card_lifecycle_notification(self, card, notification_type, subject, body):
        self.ensure_one()
        if self.email:
            email_from = (
                self.env.user.email_formatted
                or self.env.company.email_formatted
                or "no-reply@cartes-reduction.test"
            )
            self.env["mail.mail"].sudo().with_context(skip_card_eval=True).create({
                "subject": subject,
                "body_html": body,
                "email_to": self.email,
                "email_from": email_from,
            }).send()

        return self.env["notifications.client"].sudo().with_context(
            skip_card_eval=True
        ).create({
            "name": subject,
            "message": body,
            "client_id": self.id,
            "date": fields.Datetime.now(),
            "notification_type": notification_type,
        })

    def _get_pos_otp_secret(self):
        parameters = self.env["ir.config_parameter"].sudo()
        secret = parameters.get_param("cartes_reduction.pos_otp_secret")
        if not secret:
            secret = secrets.token_urlsafe(48)
            parameters.set_param("cartes_reduction.pos_otp_secret", secret)
        return secret.encode()

    def _pos_otp_digest(self, value, purpose):
        self.ensure_one()
        message = f"{purpose}:{self.id}:{value}".encode()
        return hmac.new(self._get_pos_otp_secret(), message, hashlib.sha256).hexdigest()

    def _issue_pos_otp(self):
        self.ensure_one()
        partner = self.sudo()
        now = fields.Datetime.now()
        if partner.pos_otp_locked_until and partner.pos_otp_locked_until > now:
            raise UserError("Trop de tentatives. Réessayez après la fin du délai de sécurité.")
        if not partner.email:
            raise UserError("Le client doit avoir une adresse e-mail pour recevoir son code.")

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
            "pos_otp_digest": self._pos_otp_digest(code, "otp"),
            "pos_otp_qr_digest": self._pos_otp_digest(qr_token, "qr"),
            "pos_otp_expires_at": expires_at,
            "pos_otp_attempts": 0,
            "pos_otp_locked_until": False,
        })
        body = (
            f"<p>Votre code de vérification POS est "
            f"<strong>{html.escape(code)}</strong>.</p>"
            "<p>Il expire dans 60 secondes et ne peut être utilisé qu'une fois.</p>"
            f'<p><img alt="QR code de vérification" src="{qr_data_uri}"/></p>'
        )
        self.env["mail.mail"].sudo().with_context(skip_card_eval=True).create({
            "subject": "Votre code de vérification POS",
            "body_html": body,
            "email_to": partner.email,
            "email_from": (
                self.env.company.email_formatted
                or "no-reply@cartes-reduction.test"
            ),
        }).send()
        self.env["notifications.client"].sudo().with_context(
            skip_card_eval=True
        ).create({
            "name": "Code de vérification POS envoyé",
            "message": "Un code de vérification POS valable 60 secondes a été envoyé.",
            "client_id": partner.id,
            "date": now,
            "notification_type": "pos_otp",
        })
        return {
            "code": code,
            "qr_token": qr_token,
            "qr_data_uri": qr_data_uri,
            "expires_epoch": expires_epoch,
            "expires_at": fields.Datetime.to_string(expires_at),
        }

    def action_send_pos_otp(self):
        if not self.env.user.has_group("point_of_sale.group_pos_user"):
            raise AccessError("Seuls les utilisateurs du Point de Vente peuvent envoyer un code.")
        self.ensure_one()
        issued = self._issue_pos_otp()
        return {
            "expires_epoch": issued["expires_epoch"],
            "message": "Le code de vérification a été envoyé au client.",
        }

    def verify_pos_otp_or_qr(self, code_or_qr):
        if not self.env.user.has_group("point_of_sale.group_pos_user"):
            raise AccessError("Seuls les utilisateurs du Point de Vente peuvent vérifier un code.")
        self.ensure_one()
        partner = self.sudo()
        now = fields.Datetime.now()

        if partner.pos_otp_locked_until and partner.pos_otp_locked_until > now:
            return {"success": False, "message": "Vérification temporairement verrouillée."}
        if not partner.pos_otp_expires_at or partner.pos_otp_expires_at <= now:
            return {"success": False, "message": "Le code est expiré. Demandez-en un nouveau."}
        if partner.pos_otp_attempts >= 5:
            return {"success": False, "message": "Trop de tentatives. Demandez un nouveau code plus tard."}

        candidate = str(code_or_qr or "").strip()
        is_qr = candidate.startswith("CR1.")
        expected = partner.pos_otp_qr_digest if is_qr else partner.pos_otp_digest
        actual = self._pos_otp_digest(candidate, "qr" if is_qr else "otp")
        matches = hmac.compare_digest(expected or ("0" * 64), actual)
        if is_qr:
            try:
                version, partner_id, expiry, nonce, signature = candidate.split(".")
                payload = ".".join((version, partner_id, expiry, nonce))
                expected_signature = hmac.new(
                    self._get_pos_otp_secret(),
                    payload.encode(),
                    hashlib.sha256,
                ).hexdigest()
                matches = (
                    matches
                    and version == "CR1"
                    and int(partner_id) == partner.id
                    and int(expiry) >= calendar.timegm(now.timetuple())
                    and hmac.compare_digest(signature, expected_signature)
                )
            except (TypeError, ValueError):
                matches = False

        if not matches:
            attempts = partner.pos_otp_attempts + 1
            values = {"pos_otp_attempts": attempts}
            if attempts >= 5:
                values["pos_otp_locked_until"] = now + timedelta(minutes=5)
            partner.with_context(skip_card_eval=True).write(values)
            return {"success": False, "message": "Code incorrect."}

        partner.with_context(skip_card_eval=True).write({
            "pos_otp_digest": False,
            "pos_otp_qr_digest": False,
            "pos_otp_expires_at": False,
            "pos_otp_attempts": 0,
            "pos_otp_locked_until": False,
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
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        account_link = html.escape(f"{base_url}/my/cartes", quote=True)
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
            (
                f"Votre carte Bronze {bronze.name} est active. "
                f"Votre solde initial est de {points_card.points:g} points. "
                f'<a href="{account_link}">Go to my account</a>'
            ),
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

    def _evaluate_next_card_tier(self):
        if self.env.context.get("skip_card_eval"):
            return False
        self.ensure_one()
        partner = self.commercial_partner_id
        today = fields.Date.context_today(self)
        tier_rank = {"bronze": 0, "silver": 1, "gold": 2}
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
            if next_card.is_vip:
                partner._send_vip_notification(partner, next_card, create_attribution=False)
            else:
                partner._send_card_lifecycle_notification(
                    next_card,
                    "card_upgrade",
                    f"Votre carte évolue vers {next_card.name}",
                    f"Félicitations, votre carte a été mise à niveau vers "
                    f"{next_card.name} ({next_card.discount:g} %).",
                )
            assigned_attributions = last_attribution
        return last_attribution

    def _process_card_purchase(self, order):
        if self.env.context.get("skip_card_eval"):
            return False
        self.ensure_one()
        if not order or order.loyalty_points_processed:
            return False

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
            self._send_card_lifecycle_notification(
                points_card,
                "points_awarded",
                f"{points:g} points fidélité crédités",
                f"{points:g} points ont été ajoutés après votre achat de {amount_tnd:.3f} TND.",
            )

        order.with_context(skip_card_eval=True).write({"loyalty_points_processed": True})
        self.invalidate_recordset(["total_sales", "is_vip"])
        self._compute_total_sales()
        self._compute_vip_status()
        self._evaluate_next_card_tier()
        self.with_context(skip_card_eval=True).update_behavior_analysis()
        return points

    # Analyse comportementale
    avg_purchase_value = fields.Float(
        string="Moyenne des Achats",
        compute="_compute_purchase_stats",
        store=True
    )

    purchase_frequency = fields.Float(
        string="Fréquence d'Achat (jours)",
        compute="_compute_purchase_stats",
        store=True
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
        ('churned', 'Client Inactif')
    ], string="Segment Client", compute="_compute_customer_segment", store=True)

    # Scoring RFM
    recency_score = fields.Integer(
        string="Score Récence",
        compute="_compute_rfm_scores",
        store=True
    )

    frequency_score = fields.Integer(
        string="Score Fréquence",
        compute="_compute_rfm_scores",
        store=True
    )

    monetary_score = fields.Integer(
        string="Score Montant",
        compute="_compute_rfm_scores",
        store=True
    )

    rfm_score = fields.Integer(
        string="Score RFM",
        compute="_compute_rfm_scores",
        store=True
    )

    # Prédictions
    predicted_clv = fields.Float(
        string="Valeur Vie Client Prédite",
        compute="_predict_ml_metrics",
        store=True
    )

    churn_probability = fields.Float(
        string="Probabilité de Désertion",
        compute="_predict_ml_metrics",
        store=True
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
        'sale_order_ids.amount_total',
        'sale_order_ids.state',
    )
    def _compute_total_sales(self):
        """Calcule le total des commandes confirmées, en devise du partenaire."""
        for partner in self:
            commercial_partner = partner.commercial_partner_id
            pos_orders = self.env['pos.order'].search([
                ('partner_id', 'child_of', commercial_partner.id),
                ('state', 'in', ['paid', 'done', 'invoiced']),
            ])
            sale_orders = self.env['sale.order'].search([
                ('partner_id', 'child_of', commercial_partner.id),
                ('state', 'in', ['sale', 'done']),
            ])
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

    @api.depends('total_sales')
    def _compute_vip_status(self):
        """Définit un client comme VIP si son total des achats dépasse un seuil"""
        VIP_THRESHOLD = 200  # Seuil augmenté pour un vrai cas VIP

        for record in self:
            record.is_vip = record.total_sales >= VIP_THRESHOLD

    def _send_vip_notification(self, partner, card_program, create_attribution=True):
        """Envoie la notification VIP"""
        subject = f"Félicitations {partner.name} ! Vous êtes désormais VIP"
        body = (
            f"<p>Bonjour {partner.name},</p>"
            f"<p>Votre carte VIP {card_program.name} est active "
            f"({card_program.discount:g} % de remise).</p>"
            f"<p>Total des achats : {partner.total_sales:.2f} "
            f"{partner.currency_id.symbol or ''}</p>"
        )
        partner._send_card_lifecycle_notification(
            card_program,
            "vip",
            subject,
            body,
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
        'sale_order_ids',
        'sale_order_ids.amount_total',
        'sale_order_ids.date_order',
        'sale_order_ids.state',
    )
    def _compute_purchase_stats(self):
        """Calcule les statistiques d'achat"""
        for partner in self:
            pos_orders = partner.pos_order_ids.filtered(
                lambda order: order.state in ['paid', 'done', 'invoiced'] and order.date_order
            )
            sale_orders = partner.sale_order_ids.filtered(
                lambda order: order.state in ['sale', 'done'] and order.date_order
            )
            orders = list(pos_orders) + list(sale_orders)

            # Calcul des métriques de base
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

    # Méthodes de prédiction améliorées (sans ML obligatoire)
    @api.depends('total_sales', 'purchase_frequency', 'last_purchase_date', 'is_vip', 'customer_segment')
    def _predict_metrics(self):
        """Nouvelle méthode unifiée de prédiction avec fallback"""
        for partner in self:
            try:
                # 1. Valeur Vie Client (CLV)
                partner.predicted_clv = self._calculate_clv(partner)

                # 2. Probabilité de Désertion
                partner.churn_probability = self._calculate_churn_prob(partner)

                # 3. Date de prochain achat
                partner.next_purchase_prediction = self._predict_next_purchase(partner)

            except Exception as e:
                _logger.error(f"Erreur prédiction partenaire {partner.id}: {str(e)}")
                partner.update({
                    'predicted_clv': 0,
                    'churn_probability': 0.5,
                    'next_purchase_prediction': False
                })

    def _calculate_clv(self, partner):
        """Calcul basé sur les segments et l'historique"""
        segment_factors = {
            'vip': 2.5,  # Les VIP ont un potentiel 2.5x supérieur
            'regular': 1.2,  # Clients réguliers: 1.2x
            'new': 0.8,  # Nouveaux clients: estimation prudente
            'churned': 0.3  # Clients inactifs: faible potentiel
        }
        return (partner.total_sales or 0) * segment_factors.get(partner.customer_segment, 1.0)

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
                return joblib.load(model_path)
            return None
        except Exception as e:
            _logger.error(f"Erreur chargement modèle ML: {str(e)}")
            return None

    @api.depends('total_sales', 'purchase_frequency', 'last_purchase_date', 'is_vip', 'customer_segment')
    def _predict_ml_metrics(self):
        model = self._get_ml_model()
        if not model:
            return self._predict_metrics()

        try:
            prediction_data = []
            for partner in self:
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

            for i, partner in enumerate(self):
                partner.predicted_clv = float(predictions[i][0])
                partner.churn_probability = float(predictions[i][1])
                partner.next_purchase_prediction = fields.Date.today() + timedelta(days=float(predictions[i][2]))
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
        self._compute_purchase_stats()
        self._compute_rfm_scores()
        self._compute_customer_segment()
        self._predict_ml_metrics()



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




