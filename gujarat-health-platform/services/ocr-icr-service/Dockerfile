FROM python:3.11-slim

# Tesseract 5 with English, Gujarati and Hindi models; Gujarati + Devanagari fonts for the synthetic
# scans; libraqm/fribidi for complex-script shaping in Pillow.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-eng tesseract-ocr-guj tesseract-ocr-hin \
        fonts-lohit-gujr fonts-lohit-deva fonts-dejavu-core fonts-freefont-ttf \
        libraqm0 libfribidi0 libharfbuzz0b \
    && rm -rf /var/lib/apt/lists/*

ENV OMP_THREAD_LIMIT=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8021

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8021"]
