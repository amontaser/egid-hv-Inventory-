FROM python:3.10-slim

WORKDIR /opt/hyperv_inventory

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    libffi-dev \
    gosu \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY tasks/ tasks/
COPY notifications/ notifications/
COPY templates/ templates/
COPY static/ static/
COPY wsgi.py .
COPY gunicorn.conf.py .
COPY celeryconfig.py .

RUN mkdir -p /opt/hyperv_inventory/logs /opt/hyperv_inventory/data

EXPOSE 5000

ENV PYTHONPATH=/opt/hyperv_inventory

COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
