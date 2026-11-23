from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSaleOrderLine(TransactionCase):

    def test_loyalty_reward_line_price_is_preserved(self):
        partner = self.env['res.partner'].create({'name': 'Loyalty test customer'})
        product = self.env['product.product'].create({
            'name': 'Loyalty discount line',
            'type': 'service',
            'list_price': 0,
        })
        program = self.env['loyalty.program'].create({
            'name': 'Loyalty price preservation test',
            'program_type': 'promotion',
        })
        reward = self.env['loyalty.reward'].create({
            'program_id': program.id,
            'reward_type': 'discount',
            'discount': 10,
            'discount_mode': 'percent',
            'discount_applicability': 'order',
        })
        order = self.env['sale.order'].create({'partner_id': partner.id})
        line = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': product.id,
            'name': reward.description,
            'product_uom_qty': 1,
            'price_unit': -1,
            'reward_id': reward.id,
        })

        line._compute_price()

        self.assertEqual(line.price_unit, -1)
