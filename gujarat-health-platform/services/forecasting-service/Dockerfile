FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV SERVICE_NAME=forecasting-service SERVICE_PORT=8046
EXPOSE 8046

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8046"]
