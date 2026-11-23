from odoo import SUPERUSER_ID, api

from odoo.addons.cartes_reduction.hooks import (
    configure_french_invoice_email_template,
)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    configure_french_invoice_email_template(env)
