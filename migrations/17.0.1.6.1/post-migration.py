from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    company = env["res.company"].search([
        ("name", "=", "Sotufab Distribution Tunisie"),
    ], limit=1)
    if not company:
        raise ValueError("Sotufab Distribution Tunisie company was not found.")

    website = env["website"].search([
        ("company_id", "=", company.id),
    ], limit=1)
    if not website:
        raise ValueError("The Sotufab website was not found.")

    sales_tax = env["account.tax"].search([
        ("company_id", "=", company.id),
        ("type_tax_use", "=", "sale"),
        ("amount_type", "=", "percent"),
        ("amount", "=", 19.0),
        ("active", "=", True),
    ], order="id", limit=1)
    if not sales_tax:
        raise ValueError("The active Sotufab 19% sales tax was not found.")

    values = {}
    if website.show_line_subtotals_tax_selection != "tax_included":
        values["show_line_subtotals_tax_selection"] = "tax_included"
    if not sales_tax.price_include:
        sales_tax.write({"price_include": True})
    if values:
        website.write(values)
