from odoo import SUPERUSER_ID, api

from odoo.addons.cartes_reduction.hooks import configure_tunisian_ttc_taxes


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    configure_tunisian_ttc_taxes(env)
