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
        pos_orders = self.env['pos.order'].search([
            ('date_order', '>=', cutoff),
            ('state', 'in', ['paid', 'done', 'invoiced']),
        ])
        sale_orders = self.env['sale.order'].search([
            ('date_order', '>=', cutoff),
            ('state', 'in', ['sale', 'done']),
        ])
        transactions = []
        for order in pos_orders:
            product_ids = self._transaction_product_ids(order.lines)
            if product_ids:
                transactions.append([str(product_id) for product_id in product_ids])
        for order in sale_orders:
            product_ids = self._transaction_product_ids(order.order_line)
            if product_ids:
                transactions.append([str(product_id) for product_id in product_ids])
        return transactions

    @staticmethod
    def _transaction_product_ids(order_lines):
        product_ids = []
        seen_ids = set()
        for line in order_lines:
            product = line.product_id
            if not product or (
                'reward_id' in line._fields and line.reward_id
            ) or (
                'is_reward_line' in line._fields and line.is_reward_line
            ):
                continue
            if product.id not in seen_ids:
                product_ids.append(product.id)
                seen_ids.add(product.id)
        return product_ids

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
            _logger.warning("Pas assez de transactions Web et POS pour générer des règles.")
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
            antecedents = self.env['product.product'].browse(antecedent_ids).exists()
            if len(antecedents) != len(antecedent_ids):
                _logger.warning(
                    "Skipping association rule with missing antecedent products: %s",
                    antecedent_ids,
                )
                continue
            for consequent_id in sorted(int(value) for value in rule['consequents']):
                associated_product = self.env['product.product'].browse(consequent_id).exists()
                if not associated_product:
                    _logger.warning(
                        "Skipping association rule with missing consequent product: %s",
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
                    'lift': rule['lift'],
                    'is_generated': True,
                    'card_id': self._get_appropriate_card(antecedents | associated_product),
                })

        if not new_values:
            return 0

        association_model.search([('is_generated', '=', True)]).unlink()
        return len(association_model.create(new_values))

    def _get_appropriate_card(self, products):
        names = " ".join(products.mapped('name')).casefold()
        if "café" in names or "cafe" in names or "lait" in names:
            card = self.env['cartes.reduction'].search([
                ('name', '=', 'card_coffee_addict'),
            ], limit=1)
            return card.id
        return False
