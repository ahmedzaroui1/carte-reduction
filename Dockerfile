# syntax=docker/dockerfile:1.7
#
# Odoo 17 image with the "cartes_reduction" custom module baked in.
#
#   docker compose build            # or:
#   docker build -t cartes-reduction-odoo:17 .
#
# To add another custom module later: put it next to this one in the build
# context (or a sibling folder) and add one more COPY line in the runtime stage.

# Pin the base image by digest for reproducible builds. Override with
#   --build-arg ODOO_IMAGE=odoo:17.0@sha256:<new digest>
# when you deliberately upgrade Odoo.
ARG ODOO_IMAGE=odoo:17.0@sha256:8779f8157ddcc37b84014c94646073f46fd4ad776b7a5d95fc3b0dc73a24a8fa

###############################################################################
# Stage 1 - builder: compile/download Python wheels. Compilers stay here and
# never reach the final image.
###############################################################################
FROM ${ODOO_IMAGE} AS builder

USER root
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_INPUT=1

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        python3-dev \
        libffi-dev \
        libjpeg-dev \
        zlib1g-dev

COPY requirements.txt /build/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip3 wheel --wheel-dir /wheels -r /build/requirements.txt

###############################################################################
# Stage 2 - runtime: the image that is deployed.
###############################################################################
FROM ${ODOO_IMAGE} AS runtime

LABEL org.opencontainers.image.title="cartes-reduction-odoo" \
      org.opencontainers.image.description="Odoo 17 with the cartes_reduction module" \
      org.opencontainers.image.base.name="docker.io/library/odoo:17.0"

USER root
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_INPUT=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ODOO_EXTRA_ADDONS=/opt/odoo-addons

# Apply pending Ubuntu security updates on top of the pinned base image so the
# container scan in CI does not report already-fixed OS CVEs.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends

# Install Python deps offline from the wheels built above. No "pip --upgrade":
# packages that Ubuntu already provides (cryptography, Pillow...) are kept when
# they satisfy requirements.txt, so Odoo's own system libraries are not replaced.
# qrcode is installed WITHOUT the [pil] extra on purpose: it then uses Ubuntu's
# security-patched Pillow instead of pulling a second, CVE-flagged copy from pip.
RUN --mount=type=bind,from=builder,source=/wheels,target=/wheels \
    --mount=type=bind,source=requirements.txt,target=/tmp/requirements.txt \
    pip3 install --no-cache-dir --no-index --find-links=/wheels \
        -r /tmp/requirements.txt \
    && python3 -c "import io, sklearn, joblib, pandas, mlxtend, cryptography, qrcode; \
qrcode.make('ok').save(io.BytesIO(), format='PNG')"

# Custom module(s). Code is owned by root so the "odoo" process cannot modify
# it. Add new modules here, e.g.:
#   COPY --link ./my_new_module ${ODOO_EXTRA_ADDONS}/my_new_module
COPY --link . ${ODOO_EXTRA_ADDONS}/cartes_reduction

RUN chmod -R u=rwX,go=rX ${ODOO_EXTRA_ADDONS} \
    && chmod 0755 ${ODOO_EXTRA_ADDONS}/cartes_reduction/docker/odoo-secrets-entrypoint.sh \
    # ml_model/train_model.py rewrites its CSV dataset at runtime.
    && chown -R odoo:odoo ${ODOO_EXTRA_ADDONS}/cartes_reduction/ml_model \
    # Pre-compile bytecode because the odoo user cannot write __pycache__.
    && python3 -m compileall -q ${ODOO_EXTRA_ADDONS} \
    # docker-compose lists this dir in --addons-path; Odoo refuses to start if it
    # is missing, and it only appears after a first run. Docker copies it into a
    # fresh (empty) odoo-data volume.
    && install -d -o odoo -g odoo -m 0750 /var/lib/odoo/addons /var/lib/odoo/addons/17.0

USER odoo

EXPOSE 8069 8072

# /web/health is Odoo 17's built-in liveness endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8069/web/health || exit 1