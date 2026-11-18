# Cartes de Réduction — Project Context

## Overview

**Cartes de Réduction** is an Odoo module (v1.0, LGPL-3) for managing customer discount/loyalty cards.
Author: sassi tasnim | Category: Sales

It extends Odoo's sales, point-of-sale, website, and loyalty modules to let businesses issue branded
discount cards to customers, apply discounts across POS/sales/website channels, recommend products via
association rules, predict customer behavior with ML, and audit all card operations.

## Build / Stack

- **Framework**: Odoo (Python models, XML views, HTTP controllers)
- **Manifest**: `__manifest__.py` — depends on `base`, `sale_management`, `website`, `account`,
  `point_of_sale`, `loyalty`, `product`, `pos_loyalty`, `sale`, `sale_loyalty`, `web`, `website_sale`
- **Python deps** (`requirements.txt`): `numpy<2`, `pandas==2.2.3`, `scikit-learn==1.5.2`,
  `joblib==1.4.2`, `mlxtend==0.23.4`, `qrcode[pil]>=7.4,<9`
- **External deps** declared in manifest: `sklearn`, `joblib`, `pandas`, `mlxtend`, `cryptography`
- **Entry points**: `__init__.py` imports `models`, `controllers`, `ml_model`
- **Deployment**: `Dockerfile`, `docker-compose.yml`, `Jenkinsfile`, `.env`

## Core Domain Models

| Model | Description |
|-------|-------------|
| `cartes.reduction` (`models/cartes.py`) | Discount card definition: name, code, percent, type (gold/silver/standard/premium/bronze), discount %, dates, logo, user limits, state (draft/active/archived), linked loyalty programs |
| `cartes.client` (`models/cartes_client.py`) | Client with encrypted CIN (Fernet + HMAC digest), masked CIN display, manager-only CIN access |
| `cartes.attribution` (`models/cartes_attribution.py`) | Card-to-client attribution with HMAC-signed security tokens, QR codes, nonce replay protection, renewal |
| `cartes.reduction.info` (`models/cartes_reduction_info.py`) | Card descriptive info |
| `loyalty.program` (inherited) | Extended with a `new_field` |
| `cartes.promotions` (`models/promotion.py`) | Promotions linked to a card |
| `ml.model.trainer` (`models/data_ml.py`) | ML model training wrapper |
| `product.association.generator` (`models/ProductAssociation.py`) | Association-rule generator (Apriori) |
| `carte.usage.stats` (`models/cartes_dashboard.py`) | SQL view — combined POS/sale/website card usage stats |
| `dashboard.purchase.by.customer` (`models/Dashboard_client.py`) | SQL view — products bought per customer |
| `dashboard.product.sales` (`models/dashboard_product_sales.py`) | SQL view — global product sales (POS + orders) |
| `dashboard.vente` (`models/dashboard_vente.py`) | Sales dashboard model |
| `rapports.usage` (`models/rapports.py`) | Card usage reports |
| `cartes_reduction.security.audit` (`models/security_audit.py`) | Immutable security audit log (write-once, no unlink) |
| `sale.order` (inherited) | Secure token redemption, forced discounts, loyalty program selection |
| `website` (inherited) | Partner discount cards, product-association data loading |
| Plus inherited: `product.template`, `product.product`, `product.pricelist`, `product.pricelist.item`, `res.partner`, `pos.session`, `sale.order.line`, loyalty rule/reward/program |

## Controllers (HTTP Routes)

- `controllers/main.py` — `GET /sale/order/get_cartes_data` (JSON): returns a partner's cards/loyalty programs for the UI
- `controllers/website_sale.py` — website/cart JSON endpoints:
  - `/shop/apply_loyalty` — apply best loyalty reward to an order line
  - `/shop/cart/update_discounts` — apply forced discount/rule to cart
  - `/shop/cart/update_amounts` — return cart amounts with discounts
  - `/shop/check_product_suggestions` — return product association suggestions
  - `/shop/apply_product_suggestion` — add suggested product to cart
- `controllers/block_website.py` — website access blocking

## ML Components (`ml_model/`)

1. **`train_model.py`** (`ml.model.trainer`): trains a `MultiOutputRegressor` wrapping `RandomForestRegressor`
   to predict per-customer `target_clv`, `target_churn`, and `target_days_next` from
   `total_sales`, `purchase_frequency`, `days_since_last_purchase`, `is_vip`, `customer_segment`.
   Merges CSV seed data (`dataset_clients.csv`) with live Odoo partner data; persists to `model.pkl`.
2. **`train_recommendation.py`** (`product.association.generator`): builds product association rules
   using `mlxtend` (Apriori + association_rules) from POS order history (`dataset_achats.csv`), saves rules
   into `product.association` records linked to a card.

## Security

- **CIN encryption**: `cryptography` Fernet symmetric encryption + HMAC-SHA256 digest; stored encrypted,
  digest indexed for lookup; masked display (`****1234`); manager group required for plaintext access.
- **Card tokens**: HMAC-SHA256 signed payload (`v1:<card_id>:<partner_id>:<rate>:<expiry>:<nonce>`),
  QR code image generated via `qrcode`; nonce rotation prevents replay/double-spend.
- **Concurrent redemption**: `SELECT ... FOR UPDATE NOWAIT` row lock rejects simultaneous redemptions.
- **Audit log**: `cartes_reduction.security.audit` — immutable (write/create blocked, unlink/write raise),
  logged via dedicated cursor with `SUPERUSER_ID`, events: valid redeem, tampered token, replay blocked,
  unauthorized access, expired/inactive, card issued.
- **Groups**: `cartes_manager_group` (full access), `cartes_user_group` (read/write on cards); record rule
  restricts security audit to internal users (read-only).

## Views / UI (`views/`)

- `base_menu.xml` — menu structure
- `cartes_view.xml` — card form/list views
- `cartes_client_views.xml` — client views
- `cartes_reduction_kanban.xml` — kanban board
- `cartes_dashboard.xml` — card dashboard
- `loyalty_program_views.xml` — loyalty program views
- `promotions.xml`, `fidelite.xml` — promotion/loyalty views
- `notifications.xml` — customer notifications
- `rapports.xml` + `reports/cartes_report.xml` — usage reports & report template
- `product_pricelist_views.xml` — pricelist views
- `product_template_view_inherit.xml` — product template extension
- `sale_loyalty_templates.xml`, `sale_order_views.xml` — sale order loyalty integration
- `security_audit_views.xml` — audit log views
- `website.xml` — website integration
- `dashboard_vente_views.xml`, `dashboard_client.xml`, `dashbboard_product.xml` — dashboards

## Static Assets (`static/`)

- **POS** (`point_of_sale._assets_pos`): `suggestion_card.js`, `load_carte_reduction.js`, `pos_buttons.js`,
  `pos_pop_up.js` + XML templates (`custom_discount.xml`, `reduction_button.xml`, `pos_pop_up.xml`,
  `card_suggestion_popup_templates.xml`)
- **Website** (`web.assets_frontend`): `website_sale.js`
- Images under `static/img/` and `static/description/`

## Data & Security Files

- `data/cartes_reduction_data.xml` — data records (currently empty)
- `security/security.xml` — module category + manager/user groups
- `security/ir.model.access.csv` — ACLs per model/group
- `security/record_rules.xml` — security audit read rule

## Scripts

- `scripts/verify_functional_100.py` — functional verification
- `scripts/verify_security_100.py` — security verification

## Tests

- `tests/test_sale_order_line.py` — sale order line tests

## Key Functionalities

1. **Discount card management**: create gold/silver/standard/premium/bronze cards with %, validity dates, user limits.
2. **Card attribution**: assign cards to customers with expiration, state tracking (draft/validated/expired/renewed).
3. **Secure redemption**: HMAC-signed tokens + QR codes, verified at POS/sale/website with nonce consumption and replay prevention.
4. **Card renewal**: extend expired cards by 365 days via wizard.
5. **Loyalty integration**: link cards to loyalty programs/rewards; auto-apply best discount on sale orders and website cart.
6. **Website discounts**: apply forced discounts, loyalty rewards, and product suggestions in the e-commerce cart via JSON routes.
7. **Product recommendations**: Apriori association rules from POS history, surfaced as cart suggestions.
8. **Customer ML**: predict CLV, churn probability, and days-to-next-purchase with Random Forest.
9. **Dashboards & reports**: card usage stats (POS/sale/website split), per-customer purchases, product sales.
10. **Security audit**: immutable, append-only log of all card/security events.
11. **CIN privacy**: encrypted customer national ID with role-based access.
