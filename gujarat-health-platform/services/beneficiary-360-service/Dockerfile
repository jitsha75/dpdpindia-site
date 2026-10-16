FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV SERVICE_NAME=beneficiary-360-service SERVICE_PORT=8045
EXPOSE 8045

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8045"]
