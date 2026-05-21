FROM python:3.11-slim

WORKDIR /app

# System deps for playwright, opencv, etc.
RUN apt-get update && \
    (apt-get install -y \
        ffmpeg \
        libgl1-mesa-glx \
        libglib2.0-0 \
        curl || \
     apt-get install -y \
        ffmpeg \
        libgl1-mesa-dri \
        libglib2.0-0 \
        curl) && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Increase pip timeout to reduce build failures on slow networks
ENV PIP_DEFAULT_TIMEOUT=1000
RUN pip install --no-cache-dir -r requirements.txt

# Install playwright browsers
RUN playwright install chromium --with-deps 2>/dev/null || true

COPY . .

RUN mkdir -p /app/outputs

EXPOSE 8000