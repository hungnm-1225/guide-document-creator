#!/usr/bin/env bash
# ==============================================================================
# Setup Server Script for Guide Document Creator
# Tối ưu hóa đặc biệt cho Server Ubuntu (Laptop cũ 4GB RAM + Chip i3 3rd gen)
# ==============================================================================
set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CURRENT_USER="$(whoami)"

echo "=========================================================="
echo "🚀 BẮT ĐẦU CÀI ĐẶT MÔI TRƯỜNG CHO GUIDE-DOCUMENT-CREATOR"
echo "📂 Thư mục dự án: $PROJECT_DIR"
echo "👤 User thực thi: $CURRENT_USER"
echo "=========================================================="

# 1. THIẾT LẬP BỘ ĐỆM SWAP (CỰC KỲ QUAN TRỌNG CHO MÁY 4GB RAM)
echo ""
echo "👉 [1/6] Kiểm tra và cấu hình SWAP 6GB..."
CURRENT_SWAP=$(free -m | awk '/Swap:/ {print $2}')
if [ "$CURRENT_SWAP" -lt 4000 ]; then
    echo "⚠️ Swap hiện tại ($CURRENT_SWAP MB) quá nhỏ. Đang tạo Swapfile 6GB để chống tràn RAM (OOM Crash)..."
    sudo fallocate -l 6G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=6144
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile || true
    if ! grep -q '/swapfile' /etc/fstab; then
        echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    fi
    # Tối ưu swappiness: chỉ dùng swap khi RAM thực chạm ngưỡng 85%
    sudo sysctl vm.swappiness=15
    echo "vm.swappiness=15" | sudo tee -a /etc/sysctl.conf
    echo "✅ Đã tạo thành công Swapfile 6GB!"
else
    echo "✅ Dung lượng Swap hiện tại ($CURRENT_SWAP MB) đã đạt yêu cầu an toàn."
fi

# 1.1 CHỐNG SLEEP KHI GẬP MÀN HÌNH LAPTOP (CHO SERVER CHẠY 24/7)
if [ -f "/etc/systemd/logind.conf" ]; then
    echo "💻 Cấu hình chống Sleep khi gập màn hình laptop (HandleLidSwitch=ignore)..."
    sudo sed -i 's/#HandleLidSwitch=suspend/HandleLidSwitch=ignore/' /etc/systemd/logind.conf
    sudo sed -i 's/HandleLidSwitch=suspend/HandleLidSwitch=ignore/' /etc/systemd/logind.conf
    sudo systemctl restart systemd-logind 2>/dev/null || true
fi

# 2. CÀI ĐẶT DEPENDENCIES HỆ THỐNG
echo ""
echo "👉 [2/6] Cập nhật APT và cài đặt gói hệ thống (Python, FFmpeg, Fonts, LibGL)..."
sudo apt update -y
sudo apt install -y python3 python3-pip python3-venv git ffmpeg \
    libgl1 libglib2.0-0 fonts-liberation fonts-noto-cjk fonts-dejavu-core curl

# 3. KHỞI TẠO VIRTUALENV PYTHON
echo ""
echo "👉 [3/6] Tạo môi trường Python ảo (venv)..."
cd "$PROJECT_DIR"
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt

# 4. CÀI ĐẶT PLAYWRIGHT HEADLESS CHROMIUM & THƯ VIỆN HỖ TRỢ
echo ""
echo "👉 [4/6] Cài đặt Playwright Chromium và hệ thống thư viện driver..."
playwright install chromium
sudo playwright install-deps chromium || true

# 5. KHỞI TẠO FILE .ENV NẾU CHƯA CÓ
echo ""
echo "👉 [5/6] Kiểm tra cấu hình .env..."
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo "📝 Đã tạo file .env từ .env.example. Vui lòng điền GEMINI_API_KEY vào .env sau khi setup!"
    else
        touch .env
    fi
fi

# Cấu hình tối ưu: bung toàn bộ 4 threads render video để tận dụng hết 4 luồng của chip i3
if ! grep -q "TTS_ENGINE_VI" .env; then
    echo "TTS_ENGINE_VI=edge-tts" >> .env
fi
if ! grep -q "VIDEO_THREADS" .env; then
    echo "VIDEO_THREADS=4" >> .env
fi
if ! grep -q "HEADLESS" .env; then
    echo "HEADLESS=true" >> .env
fi

# 6. THIẾT LẬP SYSTEMD SERVICE (UNLEASHED FULL POWER)
echo ""
echo "👉 [6/6] Cài đặt Systemd Service (Full CPU & RAM Performance)..."
SERVICE_SRC="$PROJECT_DIR/scripts/guide-creator.service"
SERVICE_DEST="/etc/systemd/system/guide-creator.service"

if [ -f "$SERVICE_SRC" ]; then
    # Thay thế User và Path chuẩn theo môi trường thực tế
    sudo sed -e "s|User=ubuntu|User=$CURRENT_USER|g" \
             -e "s|/home/ubuntu/projects/guide-document-creator|$PROJECT_DIR|g" \
             "$SERVICE_SRC" | sudo tee "$SERVICE_DEST" > /dev/null

    sudo systemctl daemon-reload
    sudo systemctl enable guide-creator
    sudo systemctl restart guide-creator
    echo "✅ Service guide-creator đã được kích hoạt và tự chạy nền!"
fi

echo ""
echo "=========================================================="
echo "🎉 HOÀN TẤT CÀI ĐẶT TRÊN SERVER!"
echo "🌐 Ứng dụng Web đang chạy tại: http://0.0.0.0:8888"
echo "🔍 Kiểm tra trạng thái service: sudo systemctl status guide-creator"
echo "📜 Xem log thời gian thực: sudo journalctl -u guide-creator -f"
echo "=========================================================="
