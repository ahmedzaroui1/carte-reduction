from odoo import SUPERUSER_ID, api

from odoo.addons.cartes_reduction.hooks import (
    configure_french_invoice_email_template,
)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    obsolete_access_ids = (
        "access_website_user",
        "access_sale_order_line_user",
        "access_pos_session",
        "access_sale_order",
        "access_product_association_portal",
        "access_product_association_public",
    )
    for xml_id in obsolete_access_ids:
        access = env.ref(
            f"cartes_reduction.{xml_id}",
            raise_if_not_found=False,
        )
        if access:
            access.unlink()
    configure_french_invoice_email_template(env)
