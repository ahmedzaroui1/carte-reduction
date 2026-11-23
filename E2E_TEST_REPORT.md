# Personalized Discount Cards — E2E Test Report

## Passe 3A — Part 2 (2026-11-22)

**Database:** `odoo17_e2e_tnd`
**Module version:** `17.0.1.4.0`
**Database backup:** `/home/odoo/backups/odoo17_e2e_tnd_before_3A2_20261122_141838.dump`

| Area | Result |
| --- | --- |
| Tamper-evident audit | Implemented SHA-256 chained `prev_hash` / `entry_hash` fields, deterministic genesis, UUID event identifiers, canonical payload serialization, advisory transaction locking, and chain verification. Database-only chaining does not prevent privileged DBA truncation or replacement. |
| Audit UI | Dedicated audit tree/form and `Sécurité & Audit` menu restricted to System Administrators and Cartes Managers. |
| OWASP hardening | `eval()` in sale reward domains was replaced by literal/safe domain validation; controller discount values are server-recalculated; manual cart discount commit removed; `/sale/order/get_cartes_data` and website cart line ownership are checked. |
| ML integrity | ML loading now requires a trusted SHA-256 checksum from `CARTES_ML_MODEL_SHA256` or the restricted `cartes_reduction.ml_model_sha256` parameter. Missing, invalid or mismatched checksums fail closed and generate an audit event. |
| RBAC | Custom administrative ACLs were restricted from public groups to Cartes Managers, POS-specific card read access was added, and portal partner/card/notification record rules were added. |
| Upgrade validation | Docker Compose validation, Python/XML syntax checks and module upgrade passed. |
| Automated tests | Odoo reports 54 module tests, 0 failed, 0 error(s). The 52-test baseline remains preserved. |
| Remaining limitation | True concurrent QR/OTP double-spend automation requires a non-Odoo test process separate from the module test cursor; the existing NOWAIT row-lock test passed. No external audit checkpoint or model checkpointing service was claimed. |

## Passe 3A application security (2026-11-22)

**Database:** `odoo17_e2e_tnd`
**Module version:** `17.0.1.3.0`

| Area | Verified result |
| --- | --- |
| Secret management | `CARTES_HMAC_SECRET` and `CARTES_FERNET_KEY` are supplied to Odoo by Compose from the local `.env`; the file is mode `0600`, excluded from Git and Docker build context, and its values are not stored in Odoo parameters. |
| CIN protection | CIN values are Fernet-encrypted and keyed-HMAC indexed. The legacy plaintext CIN columns were removed during upgrade; forms show masked values and restrict audited reveals to security administrators / Cartes Managers. |
| POS verification | OTP and signed QR verification use constant-time digest comparisons, order/card binding, one-time nonce rotation, a three-failure lockout, and PostgreSQL `FOR UPDATE NOWAIT` protection. POS and `/my/cartes` verification no longer request or validate a personal PIN; existing PIN hashes are retained but are not part of redemption. |
| Redemption and audit | A 60-second same-card cooldown records `VELOCITY_ABUSE`; security audit rows are immutable, and production events use an isolated cursor. |
| Database cleanup | The legacy `cartes_reduction.pos_otp_secret` parameter was removed; no pending OTP digests or plaintext CIN columns remained after upgrade. |
| E2E endpoints | `/web/login`, `/shop`, `/web/signup`, and Mailpit returned HTTP 200. Anonymous `/my/cartes` returned the expected HTTP 303 login redirect. |
| Automated tests | Odoo test statistics reported 52 tests, with 0 failures and 0 errors. |

## Final business calibration — TND E2E (2026-11-21)

**Database:** `odoo17_e2e_tnd`

**Odoo:** `http://192.168.108.129:8075`

**Mailpit:** `http://192.168.108.129:8025`

| Area | Verified result |
| --- | --- |
| Tier calibration | Bronze 2%; Silver 5% at 1,000 TND monthly spend or 2,000 points; Gold VIP 10% at 3,500 TND or 7,500 points. Each tier has its configured high-resolution artwork. |
| Points and rewards | Earning rule is 1 TND = 1 point. The active point rewards are 500 points for 10 TND and 1,500 points for 35 TND. |
| Website and POS VAT | The Tunisian 19% tax is price-included and preserved by the domestic fiscal position. A `120.000 TND` TTC price computes to `100.840 TND` untaxed plus `19.160 TND` VAT, with the same result in website, POS, and posted invoices. Catalog prices remain `120/45/450 TND`. |
| Portal | After the web-container restart, the authenticated `/my/cartes` cashier page loaded with the calibrated 5%/10% progress bars and rewards; that account has no active customer card. The deployed card overlay shows the partner name and attribution reference when a card is present. The unauthenticated route redirects to login. |
| Email rendering | Local tests verify the welcome email includes the cardholder name and attribution reference. A synthetic-only message sent to Mailpit rendered the branded Gold VIP 10% artwork, points summary, highlighted OTP box, and CTA to `/my/cartes` on port 8075. |
| Endpoints and deployment | Odoo root, `/shop`, `/web/signup`, Mailpit, and the Gold card artwork returned HTTP 200; `/my/cartes` returned the expected login redirect when unauthenticated. The E2E web container was restarted after deployment. |
| Automated tests | Odoo reports 0 failures and 0 errors across 44 module tests. |

The prior Passe 2 section records the pre-calibration 15% tier results.

## Omnichannel analytics and dashboards (2026-11-21)

| Area | Verified result |
| --- | --- |
| Purchase metrics and CLV | Confirmed Sales orders and paid POS orders are counted across commercial-partner descendants. Zero-order prospects retain zero CLV and are excluded by the removable purchaser filter. The 12-month revenue estimate uses average order value × annualized purchase cadence × (1 − churn probability); it is not profit-based CLV. |
| Apriori | Training reads confirmed web Sales and paid POS transactions. The live recalculation generated 5 rules while preserving 3 manual rules; the UI displayed all 8 with confidence, support, and lift. |
| Dashboards | The default purchaser filter shows four top-level customers and can be removed to show all 19 partners. Graph and pivot measure menus expose only Total des Achats, CLV Prédite, Moyenne des Achats, and Risque Désertion; technical fields are not aggregatable. The VIP action returns the same four top-level purchasers. The Web + POS customer-purchase view showed both sources, and the product-sales graph loaded in TND. Card-usage statistics loaded but currently had no eligible active-card discounted orders to display. |
| Refresh access | The refresh action is system-administrator-only. Its click from the current non-admin browser session returned the expected access error; an earlier admin recalculation updated 19 partners and produced the five generated rules. |
| Browser checks | The Apriori dashboard rendered 8 rows, and a reload captured no browser console or page errors. Odoo server logs still show websocket-binding errors because the evented port 8072 is not exposed by the E2E service; the tested views themselves loaded. |

**Module tests:** 44 tests, 0 failures, 0 errors on `odoo17_e2e_tnd`.

## Passe 2 baseline before final calibration (2026-11-21)

**Database:** `odoo17_e2e_tnd`

**Odoo:** `http://192.168.108.129:8075`

**Mailpit:** `http://192.168.108.129:8025`

| Scenario | Result |
| --- | --- |
| Portal signup and welcome | New signup received the Bronze welcome email with an absolute 8075 portal link. `/my/cartes` showed the digital pass, points balance, `1 TND = 1 pt`, and progress bars. |
| Website checkout and tier upgrade | Two Office Chair Blacks and two Executive Desks completed Demo Payment at `1,083.000 TND`; the customer earned 1,083 points and upgraded to Gold (15%). |
| Invoice delivery | Follow-up Gold checkout (three Executive Desks) completed at `1,147.500 TND`. Mailpit received invoice `INV/2026/00005` with the official PDF attachment at the signup customer's address. |
| POS OTP, payment, and invoice | `e2e_cashier` opened `POS/00001` in `Shop`. After OTP verification, an Executive Desk line showed the Gold 15% discount (`535.500` to `455.175 TND`). Cash payment completed as `Shop/0003`; `to_invoice` was set automatically and posted `INV/2026/00006`. Mailpit received the official PDF at the selected customer's address. |
| POS card preview and OTP | Selecting `ahmed` immediately displayed Gold 15%, `2,093.23` points, and the original/discounted totals (`142.800` / `121.380 TND`). The order stayed at 0% until Payment sent the OTP and opened the confirmation dialog. Verification applied `Remise carte : 15%` and continued directly to payment. The standalone “Envoyer Code OTP / QR” button also sent and verified an OTP. |
| Smart POS invoice and loyalty email | Cash payment completed as `Shop/0006` for `121.380 TND`; the order line records 15%, and `INV/2026/00009` is posted. Mailpit received the official PDF at `ahmed@gmail.com` and the `121.38` points notification. Odoo's standard invoice notice also went to the stale billing-contact address `demo@test.com`. |
| POS receipt and card cleanup | Automatic receipt printing is enabled on `Shop`; the paid receipt was displayed in POS. The customer now has one active Gold VIP card; older Silver and duplicate Bronze attributions are expired. Points display to two decimal places. A physical printer was not connected, so hardware printing was not independently tested. |
| Browser errors | No JS/page errors were captured during OTP verification, POS reload, or the successful payment/receipt flow. An existing `t-raw` deprecation warning remains non-blocking. |

**Module tests:** 31 tests, 0 failures, 0 errors.

**Code fixes:** consolidated the duplicate website `_cart_update` override; deduplicated and normalized active tier attributions; formatted the portal points balance; enabled automatic POS invoicing and receipt printing; and made POS/website invoice and loyalty emails use the selected customer address with cashier-safe permissions.

The earlier `odoo17_e2e` run below is retained as a separate historical report; it used a USD clone and is not the TND run above.

**Run date:** 2026-11-19

**Odoo:** 17.0

**Test database:** `odoo17_e2e` (isolated clone)

**Web UI:** `http://192.168.108.129:8075`
**Production safety:** All test records and the POS payment below are in the clone. The production database `odoo17` was not changed. No real email was sent.

## Executive summary

The requested card, portal, POS, Sales, and website scenarios were exercised. The latest module upgrade and test run completed successfully: **10 module tests, 0 failures, 0 errors**. A website cart crash found during the browser run was fixed and re-tested. The clone remains available with E2E data; it was not removed.

| Area | Result |
| --- | --- |
| Card tiers, dates, numeric conditions | Passed in the clone; currency/fixture labels need cleanup |
| Assignment, VIP and expiry notifications | State/notification behavior covered by tests; actual email delivery not tested |
| Promotions | Current/planned filters and portal promotion display passed; actual pricelist-rule application is not yet verified |
| Portal | Gold and Silver customer pages passed |
| POS | Card, 15% program discount, recommendation, and clone-only payment passed |
| Sales quotation | Draft quotation saved with a 15% line discount; clone admin needed Sales access |
| Website cart | 15% discount and product recommendation passed after fixing a server error and prompt text |
| Analytics/CLV | Model-level checks only; meaningful CLV/chart behavior remains unverified |
| Association rules | Generator saved 8 rules referencing catalog products |

## Fixes applied and automated validation

The module changes address the requested lifecycle, promotions, conditions, portal, and association-rule issues. During the E2E browser run, adding a website product also exposed an Odoo 17 error: custom code called `super()._compute_price()`, which does not exist in Odoo 17. The line discount logic now runs through a separate helper and order totals delegate to Odoo's standard `_compute_amounts()`.

The website recommendation confirmation now falls back to a product-only prompt when a rule has no linked card/reward, instead of displaying blank card/program names or `null%`.

**Validation:** upgraded `cartes_reduction` on `odoo17_e2e` and ran the full module tests: **10 tests passed, 0 failed, 0 errors**. The tests cover assignment/VIP notification, expiry notification type, eligibility thresholds, promotion states, catalog-linked rules, Sales amount computation with and without a reward, and VIP compute behavior. The Odoo process still logs existing warnings (including deprecated field `states`, an unknown `unique` parameter, and view/manifest warnings); they did not block the upgrade or tests.

## Exact scenario steps and results

### 1. Administrator — tiers and eligibility

1. Created Bronze, Silver, and Gold test tiers in the clone, with discounts of 5%, 11%, and 20%.
2. Set tier validity dates and created spend/points conditions. The Gold attribution inherited the configured end date (2027-11-19).
3. Edited the Silver tier/condition; the Silver portal account subsequently displayed the updated tier and its Gold-upgrade requirements.
4. Verified threshold evaluation in module tests, including a spend-and-points condition and an alternative qualifying condition.

**Result:** Pass. Monetary thresholds are evaluated in the company currency, which is **USD**. Some test fixture names/descriptions still say “DT” even though the UI correctly formats the values as dollars; fix those labels before presenting the data to customers.

### 2. Administrator — assignment, notifications, expiry, renewal

1. Assigned a VIP tier to the E2E customer. The attribution uses a permitted state, and the VIP notification/card assignment is covered by the workflow tests.
2. Tested assignment twice and verified that the VIP notification is not duplicated.
3. Ran the expiry behavior against an expired attribution. It moves to `expired` and creates a notification with a valid `notification_type`.
4. Notification tests stub the mail sender. No SMTP delivery or customer inbox receipt was tested.
5. The renewal path uses the permitted renewed state; a complete manual-renewal UI cycle was not independently exercised in this browser pass.

**Result:** Assignment and expiry notification-record behavior pass. Confirm renewal manually with an expired test card before relying on the complete renewal lifecycle. Email delivery remains unverified.

### 3. Administrator — promotions and pricelist

1. Opened **Promotions en cours** and the planned-promotions menu; both loaded with their date/state filters.
2. Created a planned Gold promotion for 2026-11-20 through 2026-12-31, linked it to the 15% loyalty program and the Benelux USD pricelist. The planned menu showed it.
3. Edited an active test promotion to target Gold; the Gold customer's portal then displayed it.

**Result:** Promotion state filtering, links, and portal display pass. The portal fixture is named “E2E Bronze customer offer” even though it was configured for the Gold customer; rename the fixture. The test confirmed the pricelist relation is visible, but did **not** prove that creating/editing a card promotion automatically creates or updates a real `product.pricelist.item`.

### 4. Administrator — analytics, CLV, association rules

1. Model-level dashboard checks returned 21 product-sales rows, 25 customer-purchase rows, and 0 card-usage rows. Graph screens and their filtering were not fully validated in the browser.
2. Triggered CLV/behavior analysis for a customer with no completed purchase history. The result was CLV `0.0`, churn probability `0.9`, and no next-purchase date; this is a no-history fallback, not proof of useful high-value segmentation.
3. Ran association-rule training against actual catalog products. It saved **8 rules**; examples include Office Chair Black → Desk Stand with Screen and the reverse.

**Result:** Association-rule generation/storage passes. Analytics/CLV requires a meaningful-history test and a separate visual check of each report.

### 5. Customer — portal

1. Logged in as the E2E Gold portal user and opened `/my/cartes`.
2. Verified E2E Gold, its 20% tier discount, expiry date 2027-11-19, and an eligible active promotion.
3. Logged in as the E2E Silver portal user and verified Silver details and next-tier conditions, including the Gold monthly-spend threshold.

**Result:** Pass. Portal display uses USD, but the test condition's name/description still contains “DT”; correct the fixture text.

### 6. Cashier — POS checkout

1. Opened cashier session `POS/00005` in the clone and selected **E2E VIP Assignment Test**.
2. Verified the customer's Gold card. Linked the existing **15% on next order** program to E2E Gold in the clone; after refresh, the 15% discount appeared on eligible lines.
3. Verified the POS recommendation prompt for an associated product.
4. Completed the test order using Cash: 3 lines, total **$2,610.42**, discount **$460.66**, receipt/order `00005-001-0001`.

**Result:** Pass in the isolated database. The POS card popup labels the discount total as “DTN” although the test company/pricelist use USD; correct the currency label. The payment is persisted in the clone only.

### 7. Sales quotation and website cart

**Sales quotation**

1. The first save attempt was blocked by the test administrator's missing `sale.order.option` access.
2. Added Sales User/Manager access to the test administrator in the clone and restarted the clone web service; no source access-right change was made.
3. Created draft quotation **S00046** for E2E VIP Assignment Test with Office Chair Black.
4. Verified the 15% discount on the line: `$120.50` before discount, `$102.43` untaxed after discount, `$15.36` tax, **$117.79** total. The quotation remains a draft.

**Website**

1. Logged in as the Gold portal customer and added Office Chair Black to the cart.
2. The cart showed the Gold/15% program, reducing `$120.50` to `$102.43`; total with tax was **$117.79**.
3. The first add-to-cart attempt exposed the Odoo 17 price-compute crash described above. After the fix, the website cart loaded and calculated the discount correctly.
4. Verified the recommendation prompt for **Desk Stand with Screen**. Since this rule has no linked reward metadata, the prompt now asks only whether to add the product. Dismissed the prompt; no recommendation item was added.

**Result:** Pass after the fix. The Sales access change and all test data are confined to `odoo17_e2e`.

## Remaining work / cleanup

1. Correct “DT”/“DTN” labels and the stale promotion name; amounts in this clone are USD.
2. Exercise manual renewal through the UI and test expiration notifications with a controlled mailbox after SMTP is configured.
3. Verify that promotion setup changes an actual pricelist rule and affects checkout, not just the linked pricelist field.
4. Run chart/CLV scenarios with customers who have completed purchase history; card-usage analytics currently had no rows.
5. Keep the Sales Manager grant limited to the E2E clone unless the production role design is reviewed separately.
6. Clean up the clone's test records/payment only when the clone is no longer needed. The clone and E2E records currently remain available; production was untouched.
