# Sử dụng official image của Microsoft Playwright: đã có sẵn Python 3.10, Chromium, WebKit, Firefox và toàn bộ C-dependencies
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

# Thiết lập môi trường
ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    HEADLESS=true \
    VIDEO_THREADS=4 \
    TTS_ENGINE_VI=edge-tts

# Cài đặt FFmpeg và Fonts tiếng Việt / CJK
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-liberation \
    fonts-noto-cjk \
    fonts-dejavu-core \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependencies và cài đặt
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy toàn bộ mã nguồn
COPY . .

# Expose cổng 8888
EXPOSE 8888

# Lệnh khởi chạy Web Studio
CMD ["python", "main.py", "web", "--host", "0.0.0.0", "--port", "8888", "--no-open-browser"]
