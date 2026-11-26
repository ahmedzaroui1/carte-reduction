#!/bin/sh
set -eu

mkdir -p /run/secrets
for secret_name in audit_seal_hmac_key metrics_bearer_token; do
    source_path="/run/secrets-source/${secret_name}"
    target_path="/run/secrets/${secret_name}"
    if [ ! -r "$source_path" ]; then
        printf 'Required Docker secret is unavailable: %s\n' "$secret_name" >&2
        exit 1
    fi
    cp "$source_path" "$target_path"
    chmod 0400 "$target_path"
done

exec /entrypoint.sh "$@"
