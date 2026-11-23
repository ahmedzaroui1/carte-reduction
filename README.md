# Odoo Loyalty Cards - Carte Reduction

Odoo 17 module for loyalty cards, omnichannel purchases, discounts, analytics,
and POS redemption for Sotufab Distribution Tunisie.

## Features

- Bronze, Silver, Gold VIP, and Platinum loyalty tiers
- Website, Sales, POS, and portal loyalty integration
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

Run module tests against a disposable database only:

```bash
docker compose exec web odoo \
  -c /mnt/extra-addons/cartes_reduction/odoo.conf \
  -d odoo17_e2e \
  --test-enable \
  --test-tags /cartes_reduction \
  --stop-after-init
```

Before deployment, run tests in CI and use a separate database backup and
restore procedure for production.

## Security

- Use GitHub Actions or an approved CI runner for build, test, secret scanning,
  dependency scanning, and container scanning.
- Store deployment credentials in GitHub Secrets or a secret manager.
- Do not merge or deploy directly from unvalidated local changes.
- Back up PostgreSQL and the Odoo filestore before upgrades.
