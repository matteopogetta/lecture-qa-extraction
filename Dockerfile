FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml README.md .env.example main.py ./
COPY core ./core
COPY input ./input
COPY preprocessing ./preprocessing
COPY transcription ./transcription
COPY analysis ./analysis
COPY output ./output
COPY src ./src
COPY docs ./docs
COPY sample_data ./sample_data
COPY scripts ./scripts
COPY tests ./tests

ENV PYTHONPATH=/app:/app/src

RUN pip install --no-cache-dir -e .

ENTRYPOINT ["lecture-analyzer"]
CMD ["--help"]
