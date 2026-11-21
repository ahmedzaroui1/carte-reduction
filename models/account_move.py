import base64
import html

from odoo import fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    cartes_invoice_email_sent = fields.Boolean(copy=False, readonly=True)

    def _send_cartes_invoice_email(self):
        for invoice in self:
            invoice = invoice.sudo()
            if invoice.cartes_invoice_email_sent or invoice.state != "posted":
                continue
            website_order = self.env["sale.order"].search([
                ("name", "=", invoice.invoice_origin),
                ("website_id", "!=", False),
            ], limit=1) if invoice.invoice_origin else self.env["sale.order"]
            recipient_email = (
                website_order.partner_id.email
                if website_order and website_order.partner_id.email
                else invoice.partner_id.email
            )
            if not recipient_email:
                raise UserError(
                    f"Aucune adresse e-mail n'est définie pour {invoice.partner_id.display_name}."
                )
            report = self.env.ref("account.account_invoices")
            pdf, _content_type = report._render_qweb_pdf(
                report.report_name,
                res_ids=invoice.ids,
            )
            attachment = self.env["ir.attachment"].create({
                "name": f"{invoice.name or 'Facture'}.pdf",
                "type": "binary",
                "datas": base64.b64encode(pdf),
                "res_model": "account.move",
                "res_id": invoice.id,
                "mimetype": "application/pdf",
            })
            mail = self.env["mail.mail"].create({
                "subject": f"Votre facture {invoice.name}",
                "body_html": (
                    f"<p>Bonjour {html.escape(invoice.partner_id.name or '')},</p>"
                    f"<p>Veuillez trouver ci-joint votre facture {invoice.name}.</p>"
                ),
                "email_to": recipient_email,
                "email_from": (
                    self.env.company.email_formatted
                    or "no-reply@cartes-reduction.test"
                ),
                "attachment_ids": [(4, attachment.id)],
            })
            mail.send()
            invoice.with_context(skip_card_eval=True).write({
                "cartes_invoice_email_sent": True,
            })
        return True
