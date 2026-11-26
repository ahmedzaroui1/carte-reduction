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

## Reverse proxy access

- Nginx serves the customer storefront on port `8069` and the restricted
  staff/POS portal on port `8090`.
- Set `STAFF_BIND_IP` in the untracked `.env` to the server's private interface
  address. The Nginx staff listener also allows only loopback, the local
  Docker bridge gateway, and `192.168.108.0/24`; update this allowlist in
  `nginx/odoo-security.conf` if the trusted staff network changes.
- Keep Odoo's `web` service un-published on the host. Nginx is its only
  externally reachable HTTP entry point.

## Security monitoring and external audit seal

- Provision the ignored `secrets/` files before starting the monitoring stack:
  `sh scripts/provision_monitoring_secrets.sh`. The script is idempotent and
  does not print secret values. Keep the directory mode `0700`; Docker Compose
  mounts its source files read-only, and the Odoo entrypoint copies the audit
  key and metrics token into a private `/run/secrets` tmpfs.
- The audit seal is stored in the separate `audit-seal-data` volume. After
  upgrading the module, an administrator must use **Initialiser le sceau
  externe** once, then **Vérifier l'intégrité SHA-256**. Initialization refuses
  to overwrite an existing seal.
- Prometheus and Grafana bind to `MONITORING_BIND_IP` (default
  `192.168.108.129`) and are intended for the trusted private network. Prometheus
  requires Basic Auth; Grafana requires its generated administrator password.
  Restrict these ports with the host firewall to the trusted LAN.
- `/cartes_reduction/metrics` is denied on port `8069`; Prometheus scrapes it
  through the restricted `8090` listener with a Bearer token.

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
