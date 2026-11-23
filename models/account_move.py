import base64
import html

from odoo import fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    cartes_invoice_email_sent = fields.Boolean(copy=False, readonly=True)

    def _render_cartes_invoice_email(self, recipient):
        self.ensure_one()
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        if not base_url:
            raise UserError(
                "Configurez l'URL publique du site avant l'envoi des e-mails."
            )
        base_url = base_url.rstrip("/")
        company = self.company_id
        company_name = html.escape(company.name or "")
        logo_url = html.escape(
            f"{base_url}/logo.png?company={company.id}",
            quote=True,
        )
        recipient_name = html.escape(recipient.name or "")
        invoice_name = html.escape(self.name or "")
        return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"/></head>
<body style="margin:0;padding:24px;background:#F8FAFC;font-family:Arial,Helvetica,sans-serif;color:#0F172A">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center">
<table role="presentation" width="600" cellspacing="0" cellpadding="0"
style="width:100%;max-width:600px;background:#fff;border-radius:12px;overflow:hidden">
<tr><td style="padding:20px 28px;background:#0F172A">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>
<td style="color:#fff;font-size:18px;font-weight:700">{company_name}</td>
<td align="right"><img src="{logo_url}" alt="{company_name}" width="150"
style="display:block;width:150px;max-width:100%;height:auto"/></td>
</tr></table></td></tr>
<tr><td style="padding:28px;color:#334155;font-size:15px;line-height:1.6">
<h1 style="margin:0 0 16px;color:#0F172A;font-size:22px">Votre facture {invoice_name}</h1>
<p>Bonjour {recipient_name},</p>
<p>Votre facture officielle est jointe à cet e-mail au format PDF.</p>
<p>Elle détaille les montants hors taxes, la TVA applicable et le total TTC en dinars tunisiens.</p>
<p>Merci de votre confiance.<br/><strong>{company_name}</strong></p>
</td></tr></table></td></tr></table></body></html>"""

    def _send_cartes_invoice_email(self):
        for invoice in self.sudo():
            if invoice.cartes_invoice_email_sent or invoice.state != "posted":
                continue
            standard_invoice_email = invoice.message_ids.filtered(
                lambda message: (
                    message.message_type in ("comment", "email")
                    and invoice.name
                    and invoice.name in (message.subject or "")
                )
            )
            if standard_invoice_email:
                invoice.with_context(skip_card_eval=True).write({
                    "cartes_invoice_email_sent": True,
                })
                continue

            website_order = invoice.env["sale.order"].search([
                ("name", "=", invoice.invoice_origin),
                ("website_id", "!=", False),
            ], limit=1) if invoice.invoice_origin else invoice.env["sale.order"]
            pos_order = invoice.env["pos.order"].search([
                ("account_move", "=", invoice.id),
            ], limit=1)
            recipient_partner = next(
                (
                    candidate
                    for candidate in (
                        website_order.partner_id if website_order else False,
                        pos_order.partner_id if pos_order else False,
                        invoice.partner_id,
                    )
                    if candidate and candidate.email
                ),
                invoice.partner_id,
            )
            recipient_email = recipient_partner.email
            if not recipient_email:
                raise UserError(
                    f"Aucune adresse e-mail n'est définie pour {invoice.partner_id.display_name}."
                )
            email_from = (
                invoice.company_id.email_formatted
                or invoice.company_id.partner_id.email_formatted
                or invoice.env.user.email_formatted
            )
            if not email_from:
                raise UserError(
                    f"Aucune adresse d'expédition n'est définie pour {invoice.company_id.display_name}."
                )
            report = invoice.env.ref("account.account_invoices").with_context(
                lang="fr_FR",
            )
            pdf, _content_type = report._render_qweb_pdf(
                report.report_name,
                res_ids=invoice.ids,
            )
            attachment = invoice.env["ir.attachment"].create({
                "name": f"{(invoice.name or 'Facture').replace('/', '-')}.pdf",
                "type": "binary",
                "datas": base64.b64encode(pdf),
                "res_model": "account.move",
                "res_id": invoice.id,
                "mimetype": "application/pdf",
            })
            mail = invoice.env["mail.mail"].create({
                "subject": f"Votre facture {invoice.name}",
                "body_html": invoice._render_cartes_invoice_email(recipient_partner),
                "email_to": recipient_email,
                "email_from": email_from,
                "attachment_ids": [(6, 0, [attachment.id])],
            })
            mail.send()
            invoice.with_context(skip_card_eval=True).write({
                "cartes_invoice_email_sent": True,
            })
        return True
