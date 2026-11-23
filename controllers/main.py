from odoo import http
from odoo.http import request


class LoyaltyController(http.Controller):

    @http.route('/sale/order/get_cartes_data', type='json', auth='user')
    def get_cartes_data(self, partner_id):
        try:
            partner_id = int(partner_id)
        except (TypeError, ValueError):
            return {'success': False, 'error': 'Invalid request'}

        user = request.env.user
        is_privileged = user.has_group('base.group_system') or user.has_group(
            'cartes_reduction.cartes_manager_group'
        )
        is_pos_cashier = user.has_group('point_of_sale.group_pos_user')
        if not (is_privileged or is_pos_cashier or partner_id == user.partner_id.id):
            return {'success': False, 'error': 'Unauthorized'}

        sale_order_model = request.env['sale.order']
        partner = request.env['res.partner'].browse(partner_id)
        if not partner.exists():
            return {'success': False, 'error': 'Not found'}
        if not is_privileged and partner.company_id and user.company_id not in partner.company_id:
            return {'success': False, 'error': 'Unauthorized'}
        result = sale_order_model._get_ui_cartes_data(partner_id)
        result.update({'id': partner.id, 'name': partner.name})
        return result
