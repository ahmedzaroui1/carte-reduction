# Odoo Loyalty Cards - Carte Reduction

Odoo 17 module for loyalty cards, omnichannel purchases, discounts, analytics,
and POS redemption for Sotufab Distribution Tunisie.

## Features

- Bronze, Silver, Gold VIP, and Platinum loyalty tiers
- Website, Sales, POS, and portal loyalty integration
- French invoice reports and customer invoice emails for Tunisian companies
- Portal password policy and a Sotufab-branded responsive storefront
- Customer RFM, CLV, churn, and Apriori analytics
- Signed QR/OTP POS verification and immutable security audit
- Docker Compose deployment for PostgreSQL and Odoo

## Local development

Copy the environment template securely and provide real secrets:

```bash
cp .env.example .env
chmod 600 .env
docker compose up -d --build
docker compose ps
```

Keep `.env`, database backups, private keys, Odoo configuration files, and
credentials outside Git. Never publish test or production passwords.

## Tests

Restore production data to a disposable database before running module tests:

```bash
docker compose run --rm --no-deps web odoo \
  -c /mnt/extra-addons/cartes_reduction/odoo.conf \
  --db-filter="^odoo17_disposable$" \
  -d odoo17_disposable \
  --test-enable \
  --test-tags /cartes_reduction \
  --stop-after-init
```

Do not point this command at a live or shared E2E database. Back up PostgreSQL
and the Odoo filestore before production upgrades.

## Security

- Public loyalty suggestions are validated against the current cart, website,
  active customer tier, and server-side reward configuration.
- Passwords require at least eight characters, one uppercase letter, and one
  digit.
- Model training and manual analytics refresh are restricted to system
  administrators.
- Mailpit is a development/test sink. Its web and SMTP ports bind to loopback;
  use an authenticated production SMTP server for customer delivery.
- The disposable E2E web port also binds to loopback and should not be exposed
  as a public storefront.
- On a remote host, access Mailpit through an SSH tunnel rather than publishing
  its unauthenticated UI to the network.
- Use GitHub Actions or an approved CI runner for build, test, secret scanning,
  dependency scanning, and container scanning.
- Store deployment credentials in GitHub Secrets or a secret manager.
- Do not merge or deploy directly from unvalidated local changes.
- Back up PostgreSQL and the Odoo filestore before upgrades.
