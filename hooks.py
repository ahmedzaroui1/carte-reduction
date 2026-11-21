import logging

from odoo import SUPERUSER_ID, api, fields


_logger = logging.getLogger(__name__)

POINTS_PROGRAM_NAME = "Cartes de Réduction - Points TND"


def configure_pass1_data(env):
    env = api.Environment(env.cr, SUPERUSER_ID, {})
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
        "reward_point_amount": 1.0,
        "minimum_amount": 0.0,
        "active": True,
    }
    if earning_rule:
        earning_rule.write(earning_values)
    else:
        earning_rule = env["loyalty.rule"].create(earning_values)

    points_reward = env["loyalty.reward"].search([
        ("program_id", "=", points_program.id),
        ("reward_type", "=", "discount"),
        ("discount_mode", "=", "per_order"),
        ("required_points", "=", 100),
    ], limit=1)
    points_reward_values = {
        "program_id": points_program.id,
        "reward_type": "discount",
        "discount_mode": "per_order",
        "discount": 5.0,
        "required_points": 100,
        "discount_applicability": "order",
        "description": "5 TND de remise pour 100 points",
        "active": True,
    }
    if points_reward:
        points_reward.write(points_reward_values)
    else:
        points_reward = env["loyalty.reward"].create(points_reward_values)

    tiers = (
        {
            "type": "bronze",
            "name": "E2E Bronze" if env["cartes.reduction"].search_count([("name", "=", "E2E Bronze")]) else "Bronze",
            "discount": 5.0,
            "is_vip": False,
            "conditions": [
                ("Bronze - Welcome", 0.0, 0.0, "Carte de bienvenue."),
            ],
        },
        {
            "type": "silver",
            "name": "E2E Silver" if env["cartes.reduction"].search_count([("name", "=", "E2E Silver")]) else "Silver",
            "discount": 10.0,
            "is_vip": False,
            "conditions": [
                ("Silver - Monthly Spend", 300.0, 0.0, "Dépenses mensuelles d'au moins 300 TND."),
                ("Silver - Points", 0.0, 600.0, "Solde cumulé d'au moins 600 points."),
            ],
        },
        {
            "type": "gold",
            "name": "E2E Gold" if env["cartes.reduction"].search_count([("name", "=", "E2E Gold")]) else "Gold VIP",
            "discount": 15.0,
            "is_vip": True,
            "conditions": [
                ("Gold - Monthly Spend", 1000.0, 0.0, "Dépenses mensuelles d'au moins 1 000 TND."),
                ("Gold - Points", 0.0, 1500.0, "Solde cumulé d'au moins 1 500 points."),
            ],
        },
    )

    for tier in tiers:
        program_name = f"Carte {tier['type'].title()} - {tier['discount']:.0f}%"
        tier_program = env["loyalty.program"].search([
            ("name", "=", program_name),
            ("company_id", "in", [False, company.id]),
        ], limit=1)
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

        card = env["cartes.reduction"].search([
            ("carte_type", "=", tier["type"]),
        ], order="id", limit=1)
        card_values = {
            "name": tier["name"],
            "carte_type": tier["type"],
            "discount": tier["discount"],
            "percent": tier["discount"],
            "is_vip": tier["is_vip"],
            "active": True,
            "state": "active",
            "conditions_ids": [(6, 0, condition_ids)],
            "loyalty_program_id": [(6, 0, tier_program.ids)],
        }
        if card:
            card.write(card_values)
        else:
            env["cartes.reduction"].create(card_values)

    return points_program


def configure_pass2_e2e(env):
    env = api.Environment(env.cr, SUPERUSER_ID, {})
    company = env.company
    tnd = env.ref("base.TND")
    if company.currency_id != tnd:
        raise ValueError(
            "Pass 2 shop seed requires the active E2E company currency to be TND."
        )

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
        }
        if product:
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
        "payment_provider": demo_provider,
    }


def post_init_hook(env):
    configure_pass1_data(env)
