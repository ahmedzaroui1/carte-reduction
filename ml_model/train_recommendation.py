from datetime import timedelta

import pandas as pd
from mlxtend.frequent_patterns import apriori, association_rules

from odoo import fields, models

import logging

_logger = logging.getLogger(__name__)


class ProductAssociationGenerator(models.Model):
    _name = 'product.association.generator'
    _description = "Générateur de règles d'association"

    def _get_historical_data(self):
        cutoff = fields.Datetime.now() - timedelta(days=365)
        orders = self.env['pos.order'].search([
            ('date_order', '>=', cutoff),
            ('state', 'in', ['paid', 'done', 'invoiced']),
        ])
        transactions = []
        for order in orders:
            product_ids = list(dict.fromkeys(order.lines.mapped('product_id').ids))
            if product_ids:
                transactions.append([str(product_id) for product_id in product_ids])
        return transactions

    def _preprocess_data(self, transactions):
        from mlxtend.preprocessing import TransactionEncoder

        transactions = [items for items in transactions if items]
        if not transactions:
            return pd.DataFrame()
        encoder = TransactionEncoder()
        encoded = encoder.fit(transactions).transform(transactions)
        return pd.DataFrame(encoded, columns=encoder.columns_)

    def generate_association_rules(self, min_support=0.01, min_confidence=0.7):
        _logger.info("Début de la génération des règles d'association")
        basket = self._preprocess_data(self._get_historical_data())
        if basket.empty or basket.shape[1] < 2:
            _logger.warning("Pas assez de ventes POS pour générer des règles d'association.")
            saved_count = 0
        else:
            frequent_itemsets = apriori(
                basket,
                min_support=min_support,
                use_colnames=True,
            )
            if frequent_itemsets.empty:
                saved_count = 0
            else:
                rules = association_rules(
                    frequent_itemsets,
                    metric="confidence",
                    min_threshold=min_confidence,
                )
                rules = rules[rules['antecedents'].apply(lambda items: len(items) >= 1)]
                saved_count = self._save_rules_to_odoo(rules)

        _logger.info("Règles d'association enregistrées : %s", saved_count)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': "Règles d'association",
                'message': f"{saved_count} règle(s) enregistrée(s).",
                'type': 'success' if saved_count else 'warning',
                'sticky': False,
            },
        }

    def _save_rules_to_odoo(self, rules):
        association_model = self.env['product.association']
        new_values = []
        for _, rule in rules.iterrows():
            antecedent_ids = [int(product_id) for product_id in rule['antecedents']]
            consequent_id = int(next(iter(rule['consequents'])))
            antecedents = self.env['product.product'].browse(antecedent_ids).exists()
            associated_product = self.env['product.product'].browse(consequent_id).exists()
            if len(antecedents) != len(antecedent_ids) or not associated_product:
                _logger.warning(
                    "Skipping association rule with missing catalog products: %s => %s",
                    antecedent_ids,
                    consequent_id,
                )
                continue
            if associated_product in antecedents:
                continue

            new_values.append({
                'antecedent_product_ids': [(6, 0, antecedents.ids)],
                'associated_product_id': associated_product.id,
                'confidence': rule['confidence'] * 100,
                'support': rule['support'] * 100,
                'card_id': self._get_appropriate_card(antecedents | associated_product),
            })

        if not new_values:
            return 0

        created_rules = association_model.create(new_values)
        association_model.search([('id', 'not in', created_rules.ids)]).unlink()
        return len(created_rules)

    def _get_appropriate_card(self, products):
        names = " ".join(products.mapped('name')).casefold()
        if "café" in names or "cafe" in names or "lait" in names:
            card = self.env['cartes.reduction'].search([
                ('name', '=', 'card_coffee_addict'),
            ], limit=1)
            return card.id
        return False
