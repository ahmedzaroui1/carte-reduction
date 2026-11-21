from odoo import models, fields, api

from odoo import models
class NotificationClient(models.Model):
    _name = 'notifications.client'
    _description = "Notifications des Clients"
    name = fields.Char(string="Titre")
    message = fields.Text(string="Contenu")
    client_id = fields.Many2one('res.partner', string="Client")
    date = fields.Datetime(string="Date", default=fields.Datetime.now)
    is_read = fields.Boolean(string="Lu", default=False)
    notification_type = fields.Selection([
        ('vip', 'VIP'),
        ('expired_card', 'Carte expirée'),
        ('newsletter', 'Newsletter'),
        ('welcome', 'Carte de bienvenue'),
        ('card_upgrade', 'Montée de niveau'),
        ('points_awarded', 'Points attribués'),
        ('pos_otp', 'Code de vérification POS'),
    ], string="Type de notification")
