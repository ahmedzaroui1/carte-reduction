from odoo import SUPERUSER_ID, api

from odoo.addons.cartes_reduction.hooks import (
    configure_sotufab_website_language,
)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    configure_sotufab_website_language(env)
