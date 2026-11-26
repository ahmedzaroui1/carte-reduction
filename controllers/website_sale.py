from odoo import http
from odoo.addons.website_sale.controllers.main import WebsiteSale
from odoo.http import request

from odoo.addons.cartes_reduction.models.security_utils import reject_suspicious_input


class SotufabWebsiteSale(WebsiteSale):

    @http.route()
    def shop(
        self,
        page=0,
        category=None,
        search="",
        min_price=0.0,
        max_price=0.0,
        ppg=False,
        **post,
    ):
        if reject_suspicious_input(
            request.env,
            "/shop",
            {"search": search, "query": request.httprequest.args, **post},
        ):
            return request.make_response("Requête suspecte rejetée.", status=400)
        return super().shop(
            page=page,
            category=category,
            search=search,
            min_price=min_price,
            max_price=max_price,
            ppg=ppg,
            **post,
        )


class LoyaltyCartController(http.Controller):
    @http.route('/shop/apply_loyalty', type='json', auth='public', website=True)
    def apply_loyalty(self, line_id):
        if reject_suspicious_input(
            request.env, "/shop/apply_loyalty", {"line_id": line_id}
        ):
            return request.make_response("Requête suspecte rejetée.", status=400)
        try:
            line_id = int(line_id)
        except (TypeError, ValueError):
            return {'success': False}
        order = request.website.sale_get_order()
        line = request.env['sale.order.line'].browse(line_id)
        if not order or not line.exists() or line.order_id != order:
            return {'success': False}
        if line.exists():
            loyalty_info = line._get_loyalty_info()
            if loyalty_info['best_reward']:
                # Mise à jour directe de la ligne existante
                line.write({
                    'discount': loyalty_info['max_discount'],
                    'price_unit': line.product_id.list_price,
                    'loyalty_reward_id': loyalty_info['best_reward'].id,
                    # Tu peux ajouter d'autres champs ici si tu veux afficher plus d'infos
                })

                return {
                    'success': True,
                    'discount': loyalty_info['max_discount'],
                    'program_name': loyalty_info['best_program'].name,
                    'reward_description': loyalty_info['reward_description'],
                    'card_name': loyalty_info['card_name'],

                }

        return {'success': False}

    @http.route('/shop/cart/update_discounts', type='json', auth="public", website=True)
    def update_discounts(self, force_discount=None, rule_id=None, **kw):
        if reject_suspicious_input(
            request.env,
            "/shop/cart/update_discounts",
            {"force_discount": force_discount, "rule_id": rule_id, **kw},
        ):
            return request.make_response("Requête suspecte rejetée.", status=400)
        order = request.website.sale_get_order(force_create=True)
        if not order:
            return {'success': False, 'error': 'Panier introuvable.'}

        try:
            rule_id = int(rule_id) if rule_id else False
        except (TypeError, ValueError):
            rule_id = False
        rule = (
            request.env['product.association'].sudo().browse(rule_id).exists()
            if rule_id
            else request.env['product.association']
        )
        reward = order._get_association_reward(rule) if rule else False
        server_discount = reward.discount if reward else 0.0

        order.write({
            'forced_discount': server_discount,
            'forced_discount_rule_id': rule.id if reward else False,
        })
        order = order.with_context(
            automatic_calculation=False,
            loyalty_no_messages=True,
        )
        order.order_line.filtered(lambda line: not line.is_reward_line).write({
            'discount': 0,
            'loyalty_reward_id': False,
        })
        if reward:
            order.order_line.filtered(
                lambda line: not line.is_reward_line
            ).write({
                'discount': server_discount,
                'loyalty_reward_id': reward.id,
            })

        order._compute_amounts()
        return {
            'success': True,
            'amounts': {
                'amount_total': order.amount_total,
                'amount_untaxed': order.amount_untaxed,
                'discounts': {
                    line.id: line.discount
                    for line in order.order_line
                    if not line.is_reward_line and line.discount
                },
                'forced_discount': server_discount,
            },
        }

    @http.route('/shop/cart/update_amounts', type='json', auth="public", website=True)
    def update_amounts(self):
        if reject_suspicious_input(
            request.env, "/shop/cart/update_amounts", request.params
        ):
            return request.make_response("Requête suspecte rejetée.", status=400)
        order = request.website.sale_get_order()
        if not order:
            return {'success': False}

        forced_discount = order.forced_discount or 0

        amounts = {
            'amount_untaxed': order.amount_untaxed,
            'amount_tax': order.amount_tax,
            'amount_total': order.amount_total,
            'discounts': {},
            'forced_discount': forced_discount
        }

        for line in order.order_line:
            if line.discount > 0:
                amounts['discounts'][line.id] = forced_discount if forced_discount else line.discount

        return amounts


    @http.route('/shop/check_product_suggestions', type='json', auth='public', website=True)
    def check_product_suggestions(self, product_ids, **kw):
        if reject_suspicious_input(
            request.env,
            "/shop/check_product_suggestions",
            {"product_ids": product_ids, **kw},
        ):
            return request.make_response("Requête suspecte rejetée.", status=400)
        order = request.website.sale_get_order()
        if not order:
            return {'success': False, 'suggestions': []}

        cart_product_ids = order.order_line.filtered(
            lambda line: not line.is_reward_line
        ).product_id.ids
        rules = request.env['product.association'].sudo().search([
            ('active', '=', True),
            ('antecedent_product_ids', 'in', cart_product_ids),
        ])
        website = request.website
        company = order.company_id or request.env.company
        suggestions = []
        for rule in rules:
            product = rule.associated_product_id
            template = product.product_tmpl_id
            if (
                not product.active
                or not product.sale_ok
                or not template.website_published
                or (template.website_id and template.website_id != website)
                or (product.company_id and product.company_id != company)
            ):
                continue
            reward = order._get_association_reward(
                rule, require_associated_product=False
            )
            program = (
                reward.program_id
                if reward
                else rule.card_id.loyalty_program_id.filtered(
                    lambda item: (
                        item.active
                        and item.program_type == 'next_order_coupons'
                    )
                )[:1]
            )
            suggestions.append({
                'rule_id': rule.id,
                'product_id': product.id,
                'product_name': product.display_name,
                'card_name': rule.card_id.name,
                'loyalty_program_name': program.name if program else '',
                'reward_discount': reward.discount if reward else 0,
            })
        return {'success': True, 'suggestions': suggestions}

    @http.route('/shop/apply_product_suggestion', type='json', auth='public', website=True)
    def apply_product_suggestion(self, rule_id, **kw):
        if reject_suspicious_input(
            request.env,
            "/shop/apply_product_suggestion",
            {"rule_id": rule_id, **kw},
        ):
            return request.make_response("Requête suspecte rejetée.", status=400)
        try:
            rule_id = int(rule_id)
        except (TypeError, ValueError):
            return {'success': False}

        rule = request.env['product.association'].sudo().browse(rule_id).exists()
        order = request.website.sale_get_order()
        if not rule or not rule.active or not order:
            return {'success': False}

        product = rule.associated_product_id
        template = product.product_tmpl_id
        company = order.company_id or request.env.company
        if (
            not product.active
            or not product.sale_ok
            or not template.website_published
            or (template.website_id and template.website_id != request.website)
            or (product.company_id and product.company_id != company)
        ):
            return {'success': False}

        cart_product_ids = set(
            order.order_line.filtered(
                lambda line: not line.is_reward_line
            ).product_id.ids
        )
        antecedent_ids = set(rule.antecedent_product_ids.ids)
        if (
            not antecedent_ids
            or not antecedent_ids.issubset(cart_product_ids)
            or product.id in cart_product_ids
        ):
            return {'success': False}

        order._cart_update(product_id=product.id, add_qty=1)
        reward = order._get_association_reward(rule)
        return {
            'success': True,
            'suggestion_discount': reward.discount if reward else 0,
            'rule_id': rule.id,
        }