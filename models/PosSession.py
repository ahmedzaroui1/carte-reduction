from odoo import api, fields, models
from odoo.exceptions import UserError


class PosSession(models.Model):
    _inherit = 'pos.session'

    def _pos_ui_models_to_load(self):
        result = super()._pos_ui_models_to_load()
        for model_name in [
            'cartes.attribution',
            'cartes.reduction',
            'loyalty.program',
            'loyalty.reward',
            'loyalty.card',
            'product.pricelist',
            'res.partner',
            'product.association',
        ]:
            if model_name not in result:
                result.append(model_name)
        return result

    def _loader_params_cartes_attribution(self):
        return {
            'search_params': {
                'domain': [
                    ('state', 'in', ['validated', 'renouvelé']),
                    '|',
                    ('date_expiration', '=', False),
                    ('date_expiration', '>=', fields.Date.context_today(self)),
                ],
                'fields': [
                    'client_id',
                    'carte_ids',
                    'loyalty_program_id',
                    'date_attribution',
                    'date_expiration',
                    'state',
                ],
            },
        }

    def _get_pos_ui_cartes_attribution(self, params):
        return self.env['cartes.attribution'].search_read(**params['search_params'])

    def _loader_params_cartes_reduction(self):
        return {
            'search_params': {
                'domain': [],
                'fields': [
                    'name',
                    'state',
                    'active',
                    'carte_type',
                    'discount',
                    'loyalty_program_id',
                ],
            },

        }

    def _get_pos_ui_cartes_reduction(self, params):
        return self.env['cartes.reduction'].search_read(**params['search_params'])

    def _loader_params_loyalty_program(self):
        return {
            'search_params': {
                'domain': [],
                'fields': ['name',
                'program_type',
                'reward_ids',
                'pricelist_ids',
                'date_from',
                'date_to',
                'limit_usage',
                'max_usage',

                ]
            },
        }

    def _get_pos_ui_loyalty_program(self, params):
        return self.env['loyalty.program'].search_read(**params['search_params'])

    def _loader_params_loyalty_card(self):
        return {
            'search_params': {
                'domain': [
                    ('program_id.program_type', '=', 'loyalty'),
                    '|',
                    ('expiration_date', '=', False),
                    ('expiration_date', '>=', fields.Date.context_today(self)),
                ],
                'fields': [
                    'code',
                    'partner_id',
                    'program_id',
                    'points',
                    'expiration_date',
                ],
            },
        }

    def _get_pos_ui_loyalty_card(self, params):
        return self.env['loyalty.card'].search_read(**params['search_params'])

    def _loader_params_product_association(self):
        return {
            'search_params': {
                'domain': [],
                'fields': [
                    'antecedent_product_ids', 'associated_product_id',
                    'confidence', 'support', 'lift', 'card_id'
                ],
            },
        }

    def _get_pos_ui_product_association(self, params):
        return self.env['product.association'].search_read(**params['search_params'])

    def _loader_params_res_partner(self):
        params = super()._loader_params_res_partner()
        fields_to_load = params['search_params']['fields']
        if 'parent_id' not in fields_to_load:
            fields_to_load.append('parent_id')
        return params


class PosOrder(models.Model):
    _inherit = 'pos.order'

    loyalty_points_processed = fields.Boolean(default=False, copy=False, readonly=True)
    card_redemption_order_uid = fields.Char(copy=False, readonly=True)

    @api.model
    def _order_fields(self, ui_order):
        order_values = super()._order_fields(ui_order)
        order_values["to_invoice"] = True
        order_values["card_redemption_order_uid"] = ui_order.get("uid")
        return order_values

    def _process_order(self, order, draft, existing_order):
        res = super()._process_order(order, draft, existing_order)
        if self.env.context.get("skip_card_eval") or draft or not res:
            return res

        pos_order = self.browse(res).exists()
        if pos_order and pos_order.partner_id:
            if not pos_order.loyalty_points_processed:
                pos_order.partner_id._consume_verified_pos_redemption(pos_order)
            pos_order = pos_order.sudo()
            partner = pos_order.partner_id
            partner._process_card_purchase(pos_order)
            partner._compute_purchase_stats()
            partner._compute_customer_segment()
            self.env['promotion.rule'].sudo().apply_rules(partner)
            if pos_order.state == "invoiced" and pos_order.account_move:
                pos_order.account_move._send_cartes_invoice_email()
        elif pos_order and pos_order.lines.filtered(
            lambda line: line.discount > 0 and line.custom_discount_card_id
        ):
            raise UserError("A POS card redemption requires a customer.")

        return res

    def _get_order_categories(self, order_data):
        """Helper pour récupérer les catégories de produits de la commande"""
        product_ids = [line[2]['product_id'] for line in order_data.get('lines', []) if len(line) > 2]
        products = self.env['product.product'].browse(product_ids)
        return products.mapped('categ_id').ids

class PosOrderLine(models.Model):
    _inherit = "pos.order.line"

    custom_discount_card_id = fields.Many2one('cartes.reduction', string="Carte de réduction")
    custom_discount_program_id = fields.Many2one('loyalty.program', string="Programme utilisé")

    def _export_for_ui(self, orderline):
        res = super()._export_for_ui(orderline)
        # Ajout des infos de carte de réduction
        res.update({
            'custom_discount_card_id': orderline.custom_discount_card_id.id,
            'custom_discount_program_id': orderline.custom_discount_program_id.id,
            'custom_discount_text': self._get_custom_discount_text(orderline)
        })
        return res

    def _get_custom_discount_text(self, orderline):
        if orderline.custom_discount_card_id and orderline.custom_discount_program_id:
            return f"Remise {orderline.custom_discount_card_id.name}: -{orderline.discount}%"
        return ""
