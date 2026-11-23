import base64

from odoo import fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    cartes_invoice_email_sent = fields.Boolean(copy=False, readonly=True)

    def _send_cartes_invoice_email(self):
        for invoice in self.sudo():
            if invoice.cartes_invoice_email_sent or invoice.state != "posted":
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
            report = invoice.env.ref("account.account_invoices")
            pdf, _content_type = report._render_qweb_pdf(
                report.report_name,
                res_ids=invoice.ids,
            )
            attachment = invoice.env["ir.attachment"].create({
                "name": f"{invoice.name or 'Facture'}.pdf",
                "type": "binary",
                "datas": base64.b64encode(pdf),
                "res_model": "account.move",
                "res_id": invoice.id,
                "mimetype": "application/pdf",
            })
            mail = invoice.env["mail.mail"].create({
                "subject": f"Votre facture {invoice.name}",
                "body_html": recipient_partner._render_loyalty_email(
                    f"Votre facture {invoice.name}",
                    "Votre facture officielle est jointe en PDF. "
                    "Consultez également vos cartes et votre solde de points.",
                ),
                "email_to": recipient_email,
                "email_from": (
                    invoice.company_id.email_formatted
                    or "no-reply@cartes-reduction.test"
                ),
                "attachment_ids": [(4, attachment.id)],
            })
            mail.send()
            invoice.with_context(skip_card_eval=True).write({
                "cartes_invoice_email_sent": True,
            })
        return True
