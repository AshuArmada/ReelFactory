FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# FFmpeg renders the reels; Noto fonts provide Devanagari and common Latin
# coverage for Hindi and English captions.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        fonts-liberation \
        fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

# Copy only application code and render templates. Keep brand data, API keys,
# product media, and generated output out of the image; mount those at runtime.
COPY reelfactory ./reelfactory
COPY templates ./templates

EXPOSE 5000

CMD ["python", "-m", "reelfactory", "serve", "--host", "0.0.0.0", "--port", "5000"]
