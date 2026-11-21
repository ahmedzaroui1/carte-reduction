FROM odoo:17.0

USER root

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        libffi-dev \
        libjpeg-dev \
        zlib1g-dev \
        make \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY ./requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --upgrade pip \
    && pip3 install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY . /opt/odoo-addons/cartes_reduction

RUN chown -R odoo:odoo /opt/odoo-addons/cartes_reduction

USER odoo

EXPOSE 8069
