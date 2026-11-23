from unittest.mock import patch

from odoo.tests import TransactionCase


class TestVipCompute(TransactionCase):

    def setUp(self):
        super().setUp()
        self.partner = self.env['res.partner'].with_context(skip_card_eval=True).create({
            'name': 'VIP Compute Test',
            'email': 'vip-compute-test@example.com',
        })
        self.vip_card = self.env['cartes.reduction'].create({
            'name': 'Test VIP Tier',
            'is_vip': True,
        })

    def test_vip_compute_does_not_issue_card_or_send_email(self):
        self.partner.flush_recordset(['total_sales'])
        self.env.cr.execute(
            'UPDATE res_partner SET total_sales = %s WHERE id = %s',
            (200.0, self.partner.id),
        )
        self.partner.invalidate_recordset(['total_sales', 'is_vip'])

        with patch.object(type(self.env['mail.mail']), 'send') as send_mail:
            self.partner._compute_vip_status()

        self.assertTrue(self.partner.is_vip)
        send_mail.assert_not_called()
        self.assertFalse(self.env['cartes.attribution'].search([
            ('client_id', '=', self.partner.id),
        ]))

    def test_vip_notification_assigns_card_to_many2many(self):
        with patch.object(type(self.env['mail.mail']), 'send'):
            self.partner._send_vip_notification(self.partner, self.vip_card)

        attribution = self.env['cartes.attribution'].search([
            ('client_id', '=', self.partner.id),
        ])
        self.assertEqual(attribution.carte_ids, self.vip_card)

    def test_card_report_renders_cards(self):
        report = self.env.ref('cartes_reduction.cartes_report_action')
        html, _content_type = report._render_qweb_html(
            report.report_name,
            self.vip_card.ids,
        )

        self.assertIn(b'Rapport des Cartes', html)
        self.assertIn(b'Test VIP Tier', html)
