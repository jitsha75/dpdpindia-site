# One shared image for the platform's standard Python microservices (each still runs as its own container,
# with its own database). Building once keeps a 30-service stack practical on a 16 GB laptop.
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# OCR (AP-03): Tesseract with English, Gujarati and Hindi, plus fonts for rendering synthetic Gujarati/Hindi forms
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-guj tesseract-ocr-hin \
      fonts-noto-core fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY requirements-all.txt /tmp/requirements-all.txt
# optional offline wheels (services/wheels/) are tried first; otherwise packages come from the index
COPY wheels/ /wheels/
RUN pip install --no-index --find-links=/wheels -r /tmp/requirements-all.txt || pip install --retries 10 --timeout 60 -r /tmp/requirements-all.txt
COPY . /srv/
RUN rm -rf /srv/wheels /srv/dpdp-compliance-service && useradd --system --uid 10001 app && mkdir -p /data && chown -R app /data /srv
USER app
WORKDIR /srv
