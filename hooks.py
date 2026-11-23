import base64
import logging
from pathlib import Path

from odoo import SUPERUSER_ID, api, fields

from .models.security_utils import validate_security_secrets

_logger = logging.getLogger(__name__)

POINTS_PROGRAM_NAME = "Cartes de Réduction - Points TND"
FRENCH_INVOICE_EMAIL_BODY = """
<div style="margin:0;padding:0;font-size:14px;line-height:1.6;color:#0F172A">
    <p>Bonjour <t t-out="object.partner_id.name or ''"/>,</p>
    <p>
        Votre facture
        <strong t-out="object.name or ''"/>
        est jointe à cet e-mail au format PDF.
    </p>
    <p>
        Le document présente le montant hors taxes, la TVA applicable et le
        total toutes taxes comprises en dinars tunisiens.
    </p>
    <p>Merci de votre confiance.<br/><t t-out="object.company_id.name or ''"/></p>
</div>
"""


def configure_french_invoice_email_template(env):
    french = env["res.lang"]._activate_lang("fr_FR")
    if not french:
        raise ValueError("The French (fr_FR) language is not available.")
    template = env.ref(
        "account.email_template_edi_invoice",
        raise_if_not_found=False,
    )
    if template:
        template.write({
            "subject": "Votre facture (réf. {{ object.name or 'n/a' }})",
            "lang": "fr_FR",
            "body_html": FRENCH_INVOICE_EMAIL_BODY,
        })


def configure_pass1_data(env):
    company_id = env.company.id
    env = api.Environment(
        env.cr,
        SUPERUSER_ID,
        {"allowed_company_ids": [company_id]},
    )
    mail_server_values = {
        "name": "Mailpit",
        "smtp_host": "mailpit",
        "smtp_port": 1025,
        "smtp_encryption": "none",
        "smtp_user": False,
        "smtp_pass": False,
        "sequence": 1,
        "active": True,
    }
    mail_server = env["ir.mail_server"].search([
        ("name", "=", "Mailpit"),
    ], limit=1)
    if mail_server:
        mail_server.write(mail_server_values)
    else:
        env["ir.mail_server"].create(mail_server_values)

    tnd = env.ref("base.TND", raise_if_not_found=False)
    if not tnd:
        raise ValueError("The base TND currency record is missing.")
    if not tnd.active:
        tnd.write({"active": True})

    company = env.company
    posted_moves = env["account.move"].search_count([
        ("company_id", "=", company.id),
        ("state", "=", "posted"),
    ])
    if not posted_moves:
        company.write({"currency_id": tnd.id})
    elif company.currency_id != tnd:
        _logger.warning(
            "Leaving company %s in %s because it has posted accounting moves.",
            company.display_name,
            company.currency_id.name,
        )

    points_program = env["loyalty.program"].search([
        ("name", "=", POINTS_PROGRAM_NAME),
        ("company_id", "in", [False, company.id]),
    ], limit=1)
    program_values = {
        "name": POINTS_PROGRAM_NAME,
        "program_type": "loyalty",
        "trigger": "auto",
        "currency_id": tnd.id,
        "company_id": company.id,
        "active": True,
        "pos_ok": True,
        "sale_ok": True,
        "portal_visible": True,
        "portal_point_name": "Points",
    }
    if points_program:
        points_program.write(program_values)
    else:
        points_program = env["loyalty.program"].create(program_values)

    earning_rule = env["loyalty.rule"].search([
        ("program_id", "=", points_program.id),
    ], limit=1)
    earning_values = {
        "program_id": points_program.id,
        "reward_point_mode": "money",
        "reward_point_amount": 0.1,
        "minimum_amount": 0.0,
        "active": True,
    }
    if earning_rule:
        earning_rule.write(earning_values)
    else:
        earning_rule = env["loyalty.rule"].create(earning_values)

    points_rewards = (
        (500.0, 10.0, "10 TND de remise pour 500 points"),
        (1500.0, 35.0, "35 TND de remise pour 1 500 points"),
    )
    required_point_levels = {required for required, _amount, _label in points_rewards}
    existing_points_rewards = points_program.reward_ids.filtered(
        lambda reward: (
            reward.reward_type == "discount"
            and reward.discount_mode == "per_order"
            and reward.active
        )
    )
    for reward in existing_points_rewards:
        if reward.required_points not in required_point_levels:
            reward.write({"active": False})
    for required_points, amount, description in points_rewards:
        matching_rewards = env["loyalty.reward"].with_context(
            active_test=False
        ).search([
            ("program_id", "=", points_program.id),
            ("reward_type", "=", "discount"),
            ("discount_mode", "=", "per_order"),
            ("required_points", "=", required_points),
        ], order="id")
        reward = matching_rewards[:1]
        if len(matching_rewards) > 1:
            matching_rewards[1:].write({"active": False})
        reward_values = {
            "program_id": points_program.id,
            "reward_type": "discount",
            "discount_mode": "per_order",
            "discount": amount,
            "required_points": required_points,
            "discount_applicability": "order",
            "description": description,
            "active": True,
        }
        if reward:
            reward.write(reward_values)
        else:
            env["loyalty.reward"].create(reward_values)

    tiers = (
        {
            "type": "bronze",
            "name": "Bronze",
            "discount": 2.0,
            "is_vip": False,
            "image": "card-bronze-2.png",
            "conditions": [
                ("Bronze - Welcome", 0.0, 0.0, "Carte de bienvenue."),
            ],
        },
        {
            "type": "silver",
            "name": "Silver",
            "discount": 5.0,
            "is_vip": False,
            "image": "card-silver-5.png",
            "conditions": [
                (
                    "Silver - Monthly Spend",
                    40000.0,
                    0.0,
                    "Dépenses des 30 derniers jours d'au moins 40 000 TND (ou 4 000 points cumulés).",
                ),
                (
                    "Silver - Points",
                    0.0,
                    4000.0,
                    "Solde cumulé d'au moins 4 000 points (ou 40 000 TND dépensés en 30 jours).",
                ),
            ],
        },
        {
            "type": "gold",
            "name": "Gold VIP",
            "discount": 10.0,
            "is_vip": True,
            "image": "card-gold-vip-10.png",
            "conditions": [
                (
                    "Gold - Monthly Spend",
                    60000.0,
                    0.0,
                    "Dépenses des 30 derniers jours d'au moins 60 000 TND (ou 6 000 points cumulés).",
                ),
                (
                    "Gold - Points",
                    0.0,
                    6000.0,
                    "Solde cumulé d'au moins 6 000 points (ou 60 000 TND dépensés en 30 jours).",
                ),
            ],
        },
        {
            "type": "platinum",
            "name": "Platinum",
            "discount": 16.0,
            "is_vip": True,
            "image": "card-platinum-16.png",
            "conditions": [
                (
                    "Platinum - Rolling Spend",
                    100000.0,
                    0.0,
                    "Dépenses des 30 derniers jours d'au moins 100 000 TND (ou 10 000 points cumulés).",
                ),
                (
                    "Platinum - Points",
                    0.0,
                    10000.0,
                    "Solde cumulé d'au moins 10 000 points (ou 100 000 TND dépensés en 30 jours).",
                ),
            ],
        },
    )

    for tier in tiers:
        program_name = f"Carte {tier['type'].title()} - {tier['discount']:.0f}%"
        card = env["cartes.reduction"].search([
            ("carte_type", "=", tier["type"]),
        ], order="id", limit=1)
        tier_program = card.loyalty_program_id.filtered(
            lambda program: program.program_type == "next_order_coupons"
            and program.company_id.id in (False, company.id)
        )[:1]
        if not tier_program:
            tier_program = env["loyalty.program"].search([
                ("name", "=", program_name),
                ("company_id", "in", [False, company.id]),
            ], limit=1)
        if not tier_program:
            tier_program = env["loyalty.program"].search([
                ("name", "ilike", f"Carte {tier['type'].title()} - %"),
                ("program_type", "=", "next_order_coupons"),
                ("company_id", "in", [False, company.id]),
            ], order="id", limit=1)
        tier_program_values = {
            "name": program_name,
            "program_type": "next_order_coupons",
            "trigger": "auto",
            "currency_id": tnd.id,
            "company_id": company.id,
            "active": True,
            "pos_ok": True,
            "sale_ok": True,
        }
        if tier_program:
            tier_program.write(tier_program_values)
        else:
            tier_program = env["loyalty.program"].create(tier_program_values)
        tier_program.write(tier_program_values)

        tier_reward = env["loyalty.reward"].search([
            ("program_id", "=", tier_program.id),
            ("reward_type", "=", "discount"),
        ], limit=1)
        reward_values = {
            "program_id": tier_program.id,
            "reward_type": "discount",
            "discount_mode": "percent",
            "discount": tier["discount"],
            "required_points": 1,
            "discount_applicability": "order",
            "description": f"Remise {tier['discount']:.0f}% - {tier['type'].title()}",
            "active": True,
        }
        if tier_reward:
            tier_reward.write(reward_values)
        else:
            tier_reward = env["loyalty.reward"].create(reward_values)

        conditions = env["cartes.conditions"]
        condition_ids = []
        for condition_name, min_amount, min_points, description in tier["conditions"]:
            condition = conditions.search([("name", "=", condition_name)], limit=1)
            condition_values = {
                "name": condition_name,
                "description": description,
                "min_amount": min_amount,
                "min_points": min_points,
                "currency_id": tnd.id,
                "active": True,
            }
            if condition:
                condition.write(condition_values)
            else:
                condition = conditions.create(condition_values)
            condition_ids.append(condition.id)

        image_path = (
            Path(__file__).resolve().parent
            / "static"
            / "img"
            / "cards"
            / tier["image"]
        )
        try:
            image_data = base64.b64encode(image_path.read_bytes())
        except OSError as error:
            raise ValueError(f"Unable to load tier artwork: {image_path}") from error
        card_values = {
            "name": tier["name"],
            "carte_type": tier["type"],
            "discount": tier["discount"],
            "percent": tier["discount"],
            "is_vip": tier["is_vip"],
            "active": True,
            "state": "active",
            "image_1920": image_data,
            "image": image_data,
            "logo": image_data,
            "conditions_ids": [(6, 0, condition_ids)],
            "loyalty_program_id": [(6, 0, tier_program.ids)],
        }
        if card:
            card.write(card_values)
        else:
            env["cartes.reduction"].create(card_values)

    return points_program


def _get_tnd_sales_tax(env, company, tnd):
    country = company.account_fiscal_country_id or env.ref(
        "base.tn", raise_if_not_found=False
    )
    if not country:
        raise ValueError("The company needs Tunisia as its accounting country.")

    tax = env["account.tax"].search([
        ("company_id", "=", company.id),
        ("country_id", "=", country.id),
        ("type_tax_use", "=", "sale"),
        ("amount_type", "=", "percent"),
        ("amount", "=", 19.0),
    ], order="id", limit=1)
    if tax:
        tax_values = {
            "name": "TVA 19%",
            "active": True,
            "price_include": True,
        }
        changed_values = {
            field_name: value
            for field_name, value in tax_values.items()
            if tax[field_name] != value
        }
        if changed_values:
            tax.write(changed_values)
    else:
        tax_group = env["account.tax.group"].search([
            ("country_id", "=", country.id),
            ("name", "ilike", "%19%"),
        ], order="id", limit=1)
        if not tax_group:
            tax_group = env["account.tax.group"].create({
                "name": "TVA 19%",
                "country_id": country.id,
            })
        tax = env["account.tax"].create({
            "name": "TVA 19%",
            "amount": 19.0,
            "amount_type": "percent",
            "type_tax_use": "sale",
            "company_id": company.id,
            "country_id": country.id,
            "tax_group_id": tax_group.id,
            "price_include": True,
        })
    if tax.tax_group_id.name != "TVA 19%":
        tax.tax_group_id.write({"name": "TVA 19%"})
    if tax.company_id.currency_id != tnd:
        raise ValueError("The 19% sales tax is not configured for the TND company.")
    return tax


def _apply_ttc_tax_to_sale_products(env, company, sales_tax):
    if company.account_sale_tax_id != sales_tax:
        company.write({"account_sale_tax_id": sales_tax.id})
    products = env["product.template"].with_company(company).search([
        ("sale_ok", "=", True),
        ("company_id", "in", [False, company.id]),
    ])
    products.filtered(
        lambda product: set(product.taxes_id.ids) != set(sales_tax.ids)
    ).write({"taxes_id": [(6, 0, sales_tax.ids)]})
    return products


def configure_tunisian_ttc_taxes(env):
    env = api.Environment(env.cr, SUPERUSER_ID, {})
    tunisia = env.ref("base.tn")
    tnd = env.ref("base.TND")
    companies = env["res.company"].search([("currency_id", "=", tnd.id)])
    for company in companies:
        company_country = (
            company.account_fiscal_country_id or company.partner_id.country_id
        )
        if company_country != tunisia:
            continue
        sales_tax = _get_tnd_sales_tax(env, company, tnd)
        _apply_ttc_tax_to_sale_products(env, company, sales_tax)
    return True


def configure_pass2_e2e(env):
    company_id = env.company.id
    env = api.Environment(
        env.cr,
        SUPERUSER_ID,
        {"allowed_company_ids": [company_id]},
    )
    company = env.company
    tnd = env.ref("base.TND")
    if company.currency_id != tnd:
        raise ValueError(
            "Pass 2 shop seed requires the active E2E company currency to be TND."
        )
    sales_tax = _get_tnd_sales_tax(env, company, tnd)
    _apply_ttc_tax_to_sale_products(env, company, sales_tax)
    country = company.account_fiscal_country_id or env.ref("base.tn")
    domestic_position = env["account.fiscal.position"].search([
        ("company_id", "=", company.id),
        ("country_id", "=", country.id),
        ("auto_apply", "=", True),
    ], order="sequence,id", limit=1)
    if not domestic_position:
        raise ValueError("No auto-applied domestic fiscal position exists for Tunisia.")
    domestic_position.write({
        "auto_apply": True,
        "sequence": 1,
        "vat_required": False,
    })

    parameters = env["ir.config_parameter"].sudo()
    parameters.set_param("web.base.url", "http://192.168.108.129:8075")
    parameters.set_param("web.base.url.freeze", "True")
    parameters.set_param("auth_signup.invitation_scope", "b2c")

    mail_server = env["ir.mail_server"].search([("name", "=", "Mailpit")], limit=1)
    if not mail_server:
        raise ValueError("Mailpit outgoing server is not configured.")
    mail_server.write({
        "smtp_host": "mailpit",
        "smtp_port": 1025,
        "smtp_encryption": "none",
        "active": True,
    })

    website = env["website"].search([("company_id", "=", company.id)], limit=1)
    if not website:
        raise ValueError("No website is configured for the TND E2E company.")
    pricelist = env["product.pricelist"].search([
        ("currency_id", "=", tnd.id),
        ("company_id", "in", [False, company.id]),
    ], limit=1)
    if not pricelist:
        pricelist = env["product.pricelist"].create({
            "name": "E2E TND Pricelist",
            "currency_id": tnd.id,
            "company_id": company.id,
        })
    website.write({
        "pricelist_id": pricelist.id,
        "pricelist_ids": [(4, pricelist.id)],
        "auth_signup_uninvited": "b2c",
        "show_line_subtotals_tax_selection": "tax_included",
    })

    env["pos.config"].search([
        ("company_id", "=", company.id),
        ("active", "=", True),
    ]).write({
        "iface_print_auto": True,
        "iface_tax_included": "total",
        "default_fiscal_position_id": domestic_position.id,
    })

    product_values = (
        ("Office Chair Black", 120.0),
        ("Desk Stand with Screen", 45.0),
        ("Executive Desk", 450.0),
    )
    products = env["product.template"]
    for product_name, price in product_values:
        product = env["product.template"].search([
            ("name", "=", product_name),
            ("company_id", "in", [False, company.id]),
        ], limit=1)
        values = {
            "name": product_name,
            "list_price": price,
            "sale_ok": True,
            "available_in_pos": True,
            "website_published": True,
            "website_id": website.id,
            "invoice_policy": "order",
            "company_id": company.id,
            "taxes_id": [(6, 0, sales_tax.ids)],
        }
        if product:
            values.pop("company_id")
            product.write(values)
        else:
            product = env["product.template"].create(values)
        variant = product.product_variant_id
        variant.write({
            "active": True,
            "sale_ok": True,
            "list_price": price,
        })
        products |= product

    bronze = env["cartes.reduction"].search([
        ("carte_type", "=", "bronze"),
        ("active", "=", True),
    ], order="id", limit=1)
    if not bronze:
        raise ValueError("The active Bronze tier is missing.")

    variants = products.mapped("product_variant_id")
    for antecedent, associated in (
        (variants[0], variants[1]),
        (variants[1], variants[2]),
        (variants[2], variants[0]),
    ):
        rule = env["product.association"].search([
            ("antecedent_product_ids", "in", [antecedent.id]),
            ("associated_product_id", "=", associated.id),
        ], limit=1)
        values = {
            "antecedent_product_ids": [(6, 0, [antecedent.id])],
            "associated_product_id": associated.id,
            "confidence": 75.0,
            "support": 25.0,
            "card_id": bronze.id,
        }
        if rule:
            rule.write(values)
        else:
            env["product.association"].create(values)

    demo_provider = env["payment.provider"].search([
        ("name", "=", "Demo"),
        ("company_id", "=", company.id),
    ], limit=1)
    if not demo_provider:
        installed_demo = env["payment.provider"].search([
            ("name", "=", "Demo"),
            ("module_state", "=", "installed"),
        ], order="id", limit=1)
        if installed_demo:
            demo_provider = installed_demo.copy({
                "company_id": company.id,
                "website_id": False,
            })
    if not demo_provider or demo_provider.module_state != "installed":
        raise ValueError(
            "The payment_demo dependency must be installed before enabling Demo checkout."
        )
    demo_provider.write({
        "state": "test",
        "is_published": True,
        "website_id": website.id,
        "available_currency_ids": [(4, tnd.id)],
    })
    return {
        "website": website,
        "pricelist": pricelist,
        "products": products,
        "sales_tax": sales_tax,
        "domestic_fiscal_position": domestic_position,
        "payment_provider": demo_provider,
    }


def post_init_hook(env):
    validate_security_secrets()
    configure_pass1_data(env)
    configure_tunisian_ttc_taxes(env)
    configure_french_invoice_email_template(env)
