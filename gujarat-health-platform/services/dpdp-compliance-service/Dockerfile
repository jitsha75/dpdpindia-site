# Module B: the Gujarat Health DPDP MVP as a platform microservice (own image: Python 3.12, psycopg 3).
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv
COPY requirements.txt /srv/requirements.txt
COPY wheels/ /wheels/
RUN pip install --no-index --find-links=/wheels -r /srv/requirements.txt || pip install --retries 10 --timeout 60 -r /srv/requirements.txt
RUN useradd --system --uid 10001 --home-dir /srv app && mkdir -p /data && chown app /data
COPY . /srv/
RUN rm -rf /srv/wheels
USER app
EXPOSE 8040
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8040", "--workers", "1", "--no-access-log"]
