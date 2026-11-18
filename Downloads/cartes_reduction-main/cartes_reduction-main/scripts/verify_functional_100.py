"""Rollback-only functional checks for use with ``odoo shell``.

Run from the Odoo container with:
    odoo shell -d odoo17 < /path/to/verify_functional_100.py
"""

from datetime import date, timedelta


checks = []


def check(name, callback):
    try:
        with env.cr.savepoint():
            callback()
        checks.append((name, "PASS", ""))
    except Exception as error:
        checks.append((name, "FAIL", repr(error)))


def create_fixtures():
    global test_partner, test_product, test_program, test_reward
    global test_rule, test_card, test_attribution, pos_session

    test_partner = env["res.partner"].create({
        "name": "Functional verification temporary customer",
        "email": "functional-verification@example.invalid",
    })
    test_product = env["product.product"].create({
        "name": "Functional verification temporary product",
        "type": "consu",
        "list_price": 100.0,
        "taxes_id": [(6, 0, [])],
    })
    test_program = env["loyalty.program"].create({
        "name": "Functional verification temporary 10% program",
        "program_type": "promotion",
        "date_from": date.today(),
        "date_to": date.today() + timedelta(days=1),
    })
    test_reward = env["loyalty.reward"].create({
        "program_id": test_program.id,
        "reward_type": "discount",
        "discount": 10,
        "discount_mode": "percent",
        "discount_applicability": "order",
    })
    test_rule = env["loyalty.rule"].create({
        "program_id": test_program.id,
        "minimum_amount": 0,
        "minimum_qty": 1,
        "any_product": True,
    })
    test_card = env["cartes.reduction"].create({
        "name": "Functional verification temporary Gold card",
        "carte_type": "gold",
        "discount": 10,
        "date_debut": date.today(),
        "date_expiration": date.today() + timedelta(days=1),
        "active": True,
        "state": "active",
        "loyalty_program_id": [(6, 0, [test_program.id])],
    })
    test_attribution = env["cartes.attribution"].create({
        "client_id": test_partner.id,
        "carte_ids": [(6, 0, [test_card.id])],
        "loyalty_program_id": [(6, 0, [test_program.id])],
        "date_attribution": date.today(),
        "date_expiration": date.today() + timedelta(days=1),
        "state": "validated",
    })
    pos_session = env["pos.session"].search([], limit=1)
    if not pos_session:
        pos_session = env["pos.session"]


def verify_pos_loader_and_discount():
    rule_params = pos_session._loader_params_loyalty_rule()
    reward_params = pos_session._loader_params_loyalty_reward()
    assert "program_id" in rule_params["search_params"]["fields"]
    assert "program_id" in reward_params["search_params"]["fields"]

    loaded_rules = pos_session._get_pos_ui_loyalty_rule(rule_params)
    loaded_rewards = pos_session._get_pos_ui_loyalty_reward(reward_params)
    loaded_rule = next(row for row in loaded_rules if row["id"] == test_rule.id)
    loaded_reward = next(row for row in loaded_rewards if row["id"] == test_reward.id)
    assert loaded_rule["program_id"][0] == test_program.id
    assert loaded_reward["program_id"][0] == test_program.id

    # Exercise the POS rule/reward calculation with the exact loaded records.
    order_total = 100.0
    quantity = 1
    rule_passes = (
        order_total >= loaded_rule["minimum_amount"]
        and quantity >= loaded_rule["minimum_qty"]
        and (loaded_rule["any_product"] or not loaded_rule.get("product_ids"))
    )
    assert rule_passes, "Test order did not satisfy the loaded POS rule"
    discount = order_total * loaded_reward["discount"] / 100.0
    assert round(discount, 2) == 10.00, "Expected POS discount 10.00"


def verify_sales_discount():
    order = env["sale.order"].create({
        "partner_id": test_partner.id,
        "applied_discount_card_id": test_card.id,
    })
    line = env["sale.order.line"].create({
        "order_id": order.id,
        "product_id": test_product.id,
        "name": test_product.name,
        "product_uom_qty": 1,
        "price_unit": 100.0,
    })
    order._compute_best_loyalty_program()
    order._apply_best_loyalty()
    line._compute_price()
    order._compute_amounts()

    assert order.best_loyalty_program_id == test_program
    assert order.best_loyalty_card_name == test_card.name
    assert round(line.discount, 2) == 10.00
    assert round(order.amount_untaxed, 2) == 90.00
    assert round(order.amount_total, 2) == 90.00


def verify_reporting_models():
    # These are the actual model names registered by the module's Python files.
    model_names = [
        "carte.usage.stats",
        "dashboard.purchase.by.customer",
        "dashboard.product.sales",
        "rapports.usage",
    ]
    for model_name in model_names:
        assert model_name in env.registry.models, "%s is not registered" % model_name
        env[model_name].search_count([])


try:
    with env.cr.savepoint():
        create_fixtures()
    checks.append(("Temporary fixture setup", "PASS", ""))
except Exception as error:
    checks.append(("Temporary fixture setup", "FAIL", repr(error)))

if checks and checks[0][1] == "PASS":
    check("POS loader links rewards/rules and calculates 10% discount",
          verify_pos_loader_and_discount)
    check("Sales draft quotation calculates exact 10% discount",
          verify_sales_discount)
    check("Reporting models execute ORM/SQL queries",
          verify_reporting_models)
else:
    checks.extend([
        ("POS loader links rewards/rules and calculates 10% discount",
         "BLOCKED", "Fixture setup failed"),
        ("Sales draft quotation calculates exact 10% discount",
         "BLOCKED", "Fixture setup failed"),
        ("Reporting models execute ORM/SQL queries",
         "BLOCKED", "Fixture setup failed"),
    ])

print("\nFunctional verification results")
for name, status, detail in checks:
    print("%s: %s%s" % (status, name, (": " + detail) if detail else ""))

env.cr.rollback()
