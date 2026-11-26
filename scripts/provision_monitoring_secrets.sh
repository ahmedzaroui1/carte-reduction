#!/bin/sh
set -eu
umask 077

secret_dir="${1:-./secrets}"
mkdir -p "$secret_dir"
chmod 0700 "$secret_dir"

create_secret() {
    file_path="$secret_dir/$1"
    size="$2"
    if [ -L "$file_path" ]; then
        printf 'Refusing to use a symlink as a secret: %s\n' "$file_path" >&2
        exit 1
    fi
    if [ ! -e "$file_path" ]; then
        python3 -c 'import secrets,sys; print(secrets.token_urlsafe(int(sys.argv[1])))' "$size" > "$file_path"
    fi
    if [ ! -f "$file_path" ]; then
        printf 'Secret path is not a regular file: %s\n' "$file_path" >&2
        exit 1
    fi
    chmod 0444 "$file_path"
}

create_secret audit_seal_hmac_key 64
create_secret metrics_bearer_token 48
create_secret grafana_admin_password 48
create_secret prometheus_basic_auth_password 48

prometheus_config="$secret_dir/prometheus_web_config.yml"
if [ -L "$prometheus_config" ]; then
    printf 'Refusing to use a symlink as Prometheus web config.\n' >&2
    exit 1
fi
if [ ! -e "$prometheus_config" ]; then
    hash_line="$(
        docker run --rm -i --entrypoint htpasswd httpd:2.4-alpine \
            -niB prometheus \
            < "$secret_dir/prometheus_basic_auth_password"
    )"
    password_hash="${hash_line#*:}"
    if [ -z "$password_hash" ] || [ "$password_hash" = "$hash_line" ]; then
        printf 'Failed to generate Prometheus Basic Auth configuration.\n' >&2
        exit 1
    fi
    printf 'basic_auth_users:\n  prometheus: "%s"\n' "$password_hash" > "$prometheus_config"
fi
if [ ! -f "$prometheus_config" ]; then
    printf 'Prometheus web config path is not a regular file.\n' >&2
    exit 1
fi
chmod 0444 "$prometheus_config"

printf 'Monitoring secret files are ready in %s (values not displayed).\n' "$secret_dir"
