@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title Guide Document Creator - Server Sync & Control Panel

:: ==============================================================================
:: CẤU HÌNH KẾT NỐI SERVER UBUNTU (QUA TAILSCALE / SSH)
:: Anh hãy sửa lại IP Tailscale và User tương ứng với chiếc server laptop của anh:
:: ==============================================================================
set "SERVER_USER=ubuntu"
set "SERVER_HOST=100.x.y.z"
set "REMOTE_PATH=/home/ubuntu/projects/guide-document-creator"
set "SERVICE_NAME=guide-creator"

:: File cấu hình cục bộ để lưu nếu anh nhập trực tiếp
if exist "server_config.local.bat" call "server_config.local.bat"

:MENU
cls
echo ===============================================================================
echo   🔥 GUIDE DOCUMENT CREATOR - ĐỒNG BỘ VA ĐIỀU HÀNH SERVER (UBUNTU)
echo ===============================================================================
echo   Server đích: %SERVER_USER%@%SERVER_HOST%
echo   Thư mục từ xa: %REMOTE_PATH%
echo   Service Systemd: %SERVICE_NAME%
echo -------------------------------------------------------------------------------
echo   [1] 🚀 Đồng bộ Code tức thì (Direct SSH Sync) va Restart Service
echo   [2] 🔄 Git Push (Local) va Trigger Git Pull (Server)
echo   [3] 📊 Kiểm tra Tài nguyên Server (RAM, Swap, CPU, Uptime)
echo   [4] 📜 Xem Live Logs của ứng dụng (journalctl -f)
echo   [5] 🛠️  Chạy Cài đặt Môi trường Server lần đầu (setup_server.sh)
echo   [6] 🔄 Khởi động lại / Kiểm tra trạng thái Service (systemctl)
echo   [7] ⚙️  Cấu hình lại IP / User / Thư mục Server
echo   [0] ❌ Thoát
echo ===============================================================================
set /p "CHOICE=👉 Chọn chức năng (0-7): "

if "%CHOICE%"=="1" goto SYNC_DIRECT
if "%CHOICE%"=="2" goto SYNC_GIT
if "%CHOICE%"=="3" goto CHECK_RESOURCE
if "%CHOICE%"=="4" goto VIEW_LOGS
if "%CHOICE%"=="5" goto RUN_SETUP
if "%CHOICE%"=="6" goto RESTART_SERVICE
if "%CHOICE%"=="7" goto CONFIG_SERVER
if "%CHOICE%"=="0" goto EXIT_SCRIPT

echo ⚠️ Lựa chọn không hợp lệ!
timeout /t 2 >nul
goto MENU

:CHECK_CONFIG
if "%SERVER_HOST%"=="100.x.y.z" (
    echo.
    echo ⚠️ CHÚ Ý: Anh chưa cấu hình địa chỉ IP Tailscale của Server!
    call :CONFIG_SERVER_PROMPT
)
goto :eof

:CONFIG_SERVER
cls
echo ===============================================================================
echo   ⚙️ CẤU HÌNH THÔNG TIN KẾT NỐI SERVER UBUNTU
echo ===============================================================================
:CONFIG_SERVER_PROMPT
set /p "NEW_USER=👤 Nhập tên User SSH trên server [%SERVER_USER%]: "
if not "!NEW_USER!"=="" set "SERVER_USER=!NEW_USER!"

set /p "NEW_HOST=🌐 Nhập IP Tailscale hoặc Hostname server [%SERVER_HOST%]: "
if not "!NEW_HOST!"=="" set "SERVER_HOST=!NEW_HOST!"

set /p "NEW_PATH=📂 Nhập thư mục dự án trên server [%REMOTE_PATH%]: "
if not "!NEW_PATH!"=="" set "REMOTE_PATH=!NEW_PATH!"

(
    echo set "SERVER_USER=%SERVER_USER%"
    echo set "SERVER_HOST=%SERVER_HOST%"
    echo set "REMOTE_PATH=%REMOTE_PATH%"
    echo set "SERVICE_NAME=%SERVICE_NAME%"
) > server_config.local.bat

echo ✅ Đã lưu cấu hình vào server_config.local.bat!
timeout /t 2 >nul
goto MENU

:SYNC_DIRECT
cls
call :CHECK_CONFIG
echo ===============================================================================
echo   🚀 ĐANG ĐỒNG BỘ CODE SANG SERVER: %SERVER_USER%@%SERVER_HOST%
echo ===============================================================================
echo 📦 Đang đóng gói và truyền mã nguồn (loại bỏ output, cache, .git, venv)...
tar -czf - ^
    --exclude="output" ^
    --exclude="output/*" ^
    --exclude="__pycache__" ^
    --exclude="*/__pycache__" ^
    --exclude="*.pyc" ^
    --exclude=".git" ^
    --exclude="venv" ^
    --exclude=".venv" ^
    --exclude="server_config.local.bat" ^
    . | ssh %SERVER_USER%@%SERVER_HOST% "mkdir -p %REMOTE_PATH% && tar -xzf - -C %REMOTE_PATH% && echo '✅ Code đã được đồng bộ thành công sang server!'"

if %errorlevel% neq 0 (
    echo ❌ Có lỗi xảy ra trong quá trình truyền file qua SSH!
    pause
    goto MENU
)

echo.
echo 🔄 Đang khởi động lại dịch vụ %SERVICE_NAME% trên server...
ssh %SERVER_USER%@%SERVER_HOST% "sudo systemctl restart %SERVICE_NAME% 2>/dev/null || (cd %REMOTE_PATH% && [ -d venv ] && ./venv/bin/python -m py_compile src/web/app.py && echo 'Đã kiểm tra code OK') && sudo systemctl status %SERVICE_NAME% --no-pager -n 5 2>/dev/null || true"

echo.
echo ✨ Hoàn tất đồng bộ! Ứng dụng trên server đã sẵn sàng.
pause
goto MENU

:SYNC_GIT
cls
call :CHECK_CONFIG
echo ===============================================================================
echo   🔄 ĐỒNG BỘ QUA GIT (COMMIT LOCAL PUSH ➔ SERVER PULL)
echo ===============================================================================
set /p "COMMIT_MSG=📝 Nhập thông điệp commit (Enter để lấy mặc định): "
if "!COMMIT_MSG!"=="" set "COMMIT_MSG=chore(sync): update code to server"

echo 📌 Git add & commit...
git add -A
git commit -m "!COMMIT_MSG!"
echo 🚀 Git push lên remote...
git push

echo.
echo 📥 Server git pull...
ssh %SERVER_USER%@%SERVER_HOST% "cd %REMOTE_PATH% && git pull && sudo systemctl restart %SERVICE_NAME% && echo '✅ Server đã pull và khởi động lại dịch vụ!'"

echo.
pause
goto MENU

:CHECK_RESOURCE
cls
call :CHECK_CONFIG
echo ===============================================================================
echo   📊 KIỂM TRA TÀI NGUYÊN SERVER: %SERVER_USER%@%SERVER_HOST%
echo ===============================================================================
ssh %SERVER_USER%@%SERVER_HOST% "echo '=== 1. BỘ NHỚ RAM & SWAP ===' && free -h && echo '' && echo '=== 2. TẢI CPU & THỜI GIAN HOẠT ĐỘNG (UPTIME) ===' && uptime && echo '' && echo '=== 3. DUNG LƯỢNG Ổ CỨNG ===' && df -h / && echo '' && echo '=== 4. TRẠNG THÁI SERVICE GUIDE-CREATOR ===' && (sudo systemctl status %SERVICE_NAME% --no-pager -n 8 2>/dev/null || echo 'Service chưa được đăng ký systemd')"
echo.
pause
goto MENU

:VIEW_LOGS
cls
call :CHECK_CONFIG
echo ===============================================================================
echo   📜 ĐANG THEO DÕI LIVE LOGS (Bấm Ctrl+C để thoát)
echo ===============================================================================
ssh -t %SERVER_USER%@%SERVER_HOST% "sudo journalctl -u %SERVICE_NAME% -f -n 50"
pause
goto MENU

:RUN_SETUP
cls
call :CHECK_CONFIG
echo ===============================================================================
echo   🛠️ CHẠY SCRIPT THIẾT LẬP MÔI TRƯỜNG SERVER (LẦN ĐẦU)
echo ===============================================================================
echo Bước 1: Đồng bộ file mã nguồn sang server...
tar -czf - ^
    --exclude="output" ^
    --exclude="output/*" ^
    --exclude="__pycache__" ^
    --exclude="*.pyc" ^
    --exclude=".git" ^
    --exclude="venv" ^
    --exclude=".venv" ^
    . | ssh %SERVER_USER%@%SERVER_HOST% "mkdir -p %REMOTE_PATH% && tar -xzf - -C %REMOTE_PATH%"

echo.
echo Bước 2: Chạy scripts/setup_server.sh trên server (Cần nhập mật khẩu sudo nếu có)...
ssh -t %SERVER_USER%@%SERVER_HOST% "cd %REMOTE_PATH% && chmod +x scripts/*.sh && bash scripts/setup_server.sh"

echo.
pause
goto MENU

:RESTART_SERVICE
cls
call :CHECK_CONFIG
echo ===============================================================================
echo   🔄 KHỞI ĐỘNG LẠI DỊCH VỤ SYSTEMD
echo ===============================================================================
ssh %SERVER_USER%@%SERVER_HOST% "sudo systemctl restart %SERVICE_NAME% && sudo systemctl status %SERVICE_NAME% --no-pager -n 10"
echo.
pause
goto MENU

:EXIT_SCRIPT
echo Tạm biệt anh yêu! Chúc anh có những buổi làm việc thật tuyệt vời! ❤️
timeout /t 2 >nul
exit /b 0
