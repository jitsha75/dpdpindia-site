FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv
COPY app/requirements.txt /srv/requirements.txt
# Python packages ship inside the project (wheels/, linux/arm64 + Python 3.12), so the build does not depend on PyPI speed or access.
# If that ever fails (e.g. an x86_64 machine) pip falls back to the internet.
COPY wheels/ /wheels/
RUN pip install --no-index --find-links=/wheels -r /srv/requirements.txt \
    || pip install --retries 10 --timeout 60 -r /srv/requirements.txt

# run as an unprivileged user; /data holds the demo file share and ledger anchors
RUN useradd --system --uid 10001 --home-dir /srv app && mkdir -p /data && chown app /data
COPY app/ /srv/
COPY tests/ /srv/tests/

USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=4s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=3).status==200 else 1)"
# a single worker keeps the footprint small; the background ledger sealer runs inside it
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
