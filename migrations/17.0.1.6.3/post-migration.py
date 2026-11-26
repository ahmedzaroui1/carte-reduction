import logging

from odoo import SUPERUSER_ID, api


_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    french = env["res.lang"]._activate_lang("fr_FR")
    if not french:
        raise ValueError("The French (fr_FR) language is not available.")

    company = env["res.company"].search([
        ("name", "=", "Sotufab Distribution Tunisie"),
    ], limit=1)
    if not company:
        raise ValueError("Sotufab Distribution Tunisie company was not found.")
    if company.partner_id.lang != french.code:
        company.partner_id.write({"lang": french.code})

    websites = env["website"].search([("company_id", "=", company.id)])
    if not websites:
        raise ValueError("The Sotufab website was not found.")
    for website in websites:
        values = {}
        if website.default_lang_id != french:
            values["default_lang_id"] = french.id
        if website.language_ids != french:
            values["language_ids"] = [(6, 0, french.ids)]
        if website.show_line_subtotals_tax_selection != "tax_included":
            values["show_line_subtotals_tax_selection"] = "tax_included"
        if values:
            website.write(values)

    customer_roots = env["res.partner"].search([("customer_rank", ">", 0)])
    commercial_partners = customer_roots.mapped("commercial_partner_id")
    customer_contacts = env["res.partner"].search([
        ("commercial_partner_id", "in", commercial_partners.ids),
    ])
    customer_contacts.filtered(lambda partner: partner.lang != french.code).write({
        "lang": french.code,
    })

    tunisia = env["res.country"].search([("code", "=", "TN")], limit=1)
    if not tunisia:
        raise ValueError("Tunisia country record was not found.")
    demo_addresses = env["res.partner"].search([
        ("company_id", "=", company.id),
        ("street", "=", "Rue des Bourlottes 9"),
        ("zip", "=", "1367"),
        ("city", "=", "Ramillies"),
        ("country_id.code", "=", "BE"),
    ])
    if demo_addresses:
        demo_addresses.write({
            "street": False,
            "street2": False,
            "zip": "1000",
            "city": "Tunis",
            "state_id": False,
            "country_id": tunisia.id,
            "lang": french.code,
        })
        _logger.info(
            "Updated %s demo billing addresses from Belgium to Tunis.",
            len(demo_addresses),
        )

    sales_tax = env["account.tax"].search([
        ("company_id", "=", company.id),
        ("type_tax_use", "=", "sale"),
        ("amount_type", "=", "percent"),
        ("amount", "=", 19.0),
        ("active", "=", True),
    ], order="id", limit=1)
    if not sales_tax:
        raise ValueError("The active Sotufab 19% sales tax was not found.")
    french_sales_tax = sales_tax.with_context(lang=french.code)
    tax_values = {}
    if not sales_tax.price_include:
        tax_values["price_include"] = True
    if french_sales_tax.invoice_label != "TVA 19%":
        tax_values["invoice_label"] = "TVA 19%"
    if tax_values:
        french_sales_tax.write(tax_values)

    immediate_term = env["account.payment.term"].with_context(lang="en_US").search([
        ("name", "=", "Immediate Payment"),
        ("company_id", "in", [False, company.id]),
    ], limit=1)
    if not immediate_term:
        raise ValueError("The Immediate Payment term was not found.")
    french_term = immediate_term.with_context(lang=french.code)
    payment_note = "<p>Conditions de règlement : Paiement immédiat</p>"
    if french_term.note != payment_note:
        french_term.write({"note": payment_note})
