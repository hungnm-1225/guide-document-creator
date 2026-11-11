#!/usr/bin/env python3
"""
Guide Document Creator - Server Sync & Control Panel
Điều khiển, đồng bộ mã nguồn và quản lý tài nguyên server Ubuntu qua SSH / Tailscale.
"""
import os
import sys
import json
import shutil
import tempfile
import subprocess
from pathlib import Path

# Thư mục gốc dự án
BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "server_config.json"

# Cấu hình mặc định
DEFAULT_CONFIG = {
    "server_user": "hungnm2512",
    "server_host": "100.85.115.66",
    "remote_path": "/home/hungnm2512/projects/guide-document-creator",
    "service_name": "guide-creator"
}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                return {**DEFAULT_CONFIG, **cfg}
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    print("\n✅ Đã lưu cấu hình vào server_config.json!")


def get_target(cfg: dict) -> str:
    return f"{cfg['server_user']}@{cfg['server_host']}"


def print_banner(cfg: dict):
    os.system("cls" if os.name == "nt" else "clear")
    target = get_target(cfg)
    print("=" * 79)
    print("  🔥 GUIDE DOCUMENT CREATOR - ĐIỀU HÀNH & ĐỒNG BỘ SERVER (UBUNTU)")
    print("=" * 79)
    print(f"  📡 Server đích   : {target}")
    print(f"  📂 Thư mục từ xa : {cfg['remote_path']}")
    print(f"  ⚙️  Systemd Service: {cfg['service_name']}")
    print("-" * 79)


def run_cmd(cmd, check=False, shell=False):
    """Chạy command và hiển thị trực tiếp ra console"""
    return subprocess.run(cmd, shell=shell, check=check)


def setup_ssh_key(cfg: dict):
    print_banner(cfg)
    print("🔑 CÀI ĐẶT KHÓA SSH TỰ ĐỘNG (ĐĂNG NHẬP KHÔNG CẦN PASSWORD)")
    print("-" * 79)

    ssh_dir = Path.home() / ".ssh"
    pub_key_file = ssh_dir / "id_ed25519.pub"
    if not pub_key_file.exists():
        pub_key_file = ssh_dir / "id_rsa.pub"

    if not pub_key_file.exists():
        print("👉 Đang tạo khóa SSH mới (ed25519)...")
        ssh_dir.mkdir(parents=True, exist_ok=True)
        key_path = ssh_dir / "id_ed25519"
        run_cmd(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key_path)])
        pub_key_file = ssh_dir / "id_ed25519.pub"

    if not pub_key_file.exists():
        print("❌ Không tìm thấy file khóa công khai SSH!")
        input("\nBấm Enter để quay lại...")
        return

    pub_key = pub_key_file.read_text(encoding="utf-8").strip()
    target = get_target(cfg)
    print(f"👉 Khóa SSH của bạn: {pub_key[:40]}...{pub_key[-20:]}")
    print(f"\n👉 Đang gửi khóa sang server {target}...")
    print("⚠️  Vui lòng nhập mật khẩu tài khoản server lần này (chỉ cần nhập 1 lần duy nhất):")

    remote_sh = (
        f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
        f"echo '{pub_key}' >> ~/.ssh/authorized_keys && "
        f"sort -u ~/.ssh/authorized_keys -o ~/.ssh/authorized_keys && "
        f"chmod 600 ~/.ssh/authorized_keys && echo 'SSH_KEY_SUCCESS'"
    )
    res = run_cmd(["ssh", "-o", "StrictHostKeyChecking=accept-new", target, f"bash -c \"{remote_sh}\""])
    if res.returncode == 0:
        print("\n🎉 THÀNH CÔNG! Từ giờ bạn có thể kết nối server không cần gõ mật khẩu!")
    else:
        print("\n⚠️ Cài đặt khóa SSH thất bại hoặc mật khẩu chưa đúng.")
    input("\nBấm Enter để tiếp tục...")


def sync_direct(cfg: dict):
    print_banner(cfg)
    print("🚀 ĐỒNG BỘ CODE TỨC THÌ (DIRECT SSH SYNC) & RESTART SERVICE")
    print("-" * 79)

    target = get_target(cfg)
    remote_path = cfg["remote_path"]
    service_name = cfg["service_name"]

    temp_tar = Path(tempfile.gettempdir()) / "deploy_payload.tar.gz"
    if temp_tar.exists():
        temp_tar.unlink()

    print("📦 [1/3] Đang đóng gói mã nguồn (loại bỏ thư mục rác, cache, output, venv)...")
    tar_cmd = [
        "tar", "-czf", str(temp_tar),
        "--exclude=output",
        "--exclude=output/*",
        "--exclude=__pycache__",
        "--exclude=*/__pycache__",
        "--exclude=*.pyc",
        "--exclude=.git",
        "--exclude=venv",
        "--exclude=.venv",
        "--exclude=server_config.json",
        "-C", str(BASE_DIR),
        "."
    ]
    res = run_cmd(tar_cmd)
    if res.returncode != 0 or not temp_tar.exists():
        print("❌ Lỗi khi đóng gói file tar!")
        input("\nBấm Enter để quay lại...")
        return

    print("📤 [2/3] Đang truyền gói cập nhật sang server qua Tailscale...")
    res = run_cmd(["scp", "-o", "StrictHostKeyChecking=accept-new", str(temp_tar), f"{target}:/tmp/deploy_payload.tar.gz"])
    if temp_tar.exists():
        temp_tar.unlink()

    if res.returncode != 0:
        print("❌ Lỗi khi truyền file qua SCP!")
        input("\nBấm Enter để quay lại...")
        return

    print("⚙️  [3/3] Đang giải nén và khởi động lại dịch vụ...")
    remote_script = (
        f"mkdir -p {remote_path} && "
        f"tar -xzf /tmp/deploy_payload.tar.gz -C {remote_path} && "
        f"rm -f /tmp/deploy_payload.tar.gz && "
        f"sudo systemctl restart {service_name} 2>/dev/null || true && "
        f"echo 'SYNC_DONE'"
    )
    run_cmd(["ssh", "-o", "StrictHostKeyChecking=accept-new", target, f"bash -c \"{remote_script}\""])

    print("\n" + "=" * 79)
    print("✨ ĐỒNG BỘ HOÀN TẤT! Ứng dụng đã được cập nhật.")
    print(f"🌐 Truy cập giao diện: http://{cfg['server_host']}:8000")
    print("=" * 79)
    input("\nBấm Enter để quay lại Menu...")


def sync_git(cfg: dict):
    print_banner(cfg)
    print("🔄 ĐỒNG BỘ QUA GIT (LOCAL PUSH ➔ SERVER PULL)")
    print("-" * 79)

    msg = input("📝 Nhập thông điệp commit [Enter để lấy mặc định 'chore: sync to server']: ").strip()
    if not msg:
        msg = "chore: sync to server"

    print("👉 1. Git add & commit...")
    run_cmd(["git", "add", "-A"])
    run_cmd(["git", "commit", "-m", msg])

    print("👉 2. Git push lên GitHub...")
    run_cmd(["git", "push"])

    print("👉 3. Server git pull & restart service...")
    target = get_target(cfg)
    remote_cmd = (
        f"cd {cfg['remote_path']} && git pull && "
        f"sudo systemctl restart {cfg['service_name']} && echo 'GIT_PULL_DONE'"
    )
    run_cmd(["ssh", "-o", "StrictHostKeyChecking=accept-new", target, f"bash -c \"{remote_cmd}\""])

    print("\n✨ Hoàn tất đồng bộ Git!")
    input("\nBấm Enter để quay lại Menu...")


def check_resources(cfg: dict):
    print_banner(cfg)
    target = get_target(cfg)
    print(f"📊 KIỂM TRA TÀI NGUYÊN SERVER: {target}")
    print("-" * 79)

    remote_sh = (
        "echo '=== 1. RAM & BỘ NHỚ SWAP ===' && free -h && "
        "echo '' && echo '=== 2. CPU & UPTIME ===' && uptime && "
        "echo '' && echo '=== 3. Ổ CỨNG (DISK USAGE) ===' && df -h / && "
        f"echo '' && echo '=== 4. TRẠNG THÁI SERVICE {cfg['service_name']} ===' && "
        f"(sudo systemctl status {cfg['service_name']} --no-pager -n 8 2>/dev/null || echo 'Service chưa được đăng ký')"
    )
    run_cmd(["ssh", "-o", "StrictHostKeyChecking=accept-new", target, f"bash -c \"{remote_sh}\""])
    input("\nBấm Enter để quay lại Menu...")


def view_logs(cfg: dict):
    print_banner(cfg)
    target = get_target(cfg)
    print("📜 THEO DÕI LIVE LOGS (Bấm Ctrl+C để thoát về menu)")
    print("-" * 79)
    try:
        run_cmd(["ssh", "-t", "-o", "StrictHostKeyChecking=accept-new", target, f"sudo journalctl -u {cfg['service_name']} -f -n 50"])
    except KeyboardInterrupt:
        pass
    input("\nBấm Enter để quay lại Menu...")


def run_setup(cfg: dict):
    print_banner(cfg)
    target = get_target(cfg)
    print("🛠️ CÀI ĐẶT MÔI TRƯỜNG SERVER LẦN ĐẦU (SETUP_SERVER.SH)")
    print("-" * 79)
    print("Quy trình tự động gồm:")
    print("  1. Kiểm tra bộ đệm Swap chống tràn RAM cho chip i3.")
    print("  2. Cài đặt Python 3, venv, FFmpeg, fonts, Playwright Chromium.")
    print("  3. Chống sleep khi gập màn hình laptop (HandleLidSwitch=ignore).")
    print(f"  4. Kích hoạt Systemd Service {cfg['service_name']} (Bung 100% Full CPU & RAM).")
    print("")

    confirm = input(f"👉 Xác nhận bắt đầu cài đặt lên {target}? (Y/N): ").strip().lower()
    if confirm != "y":
        return

    temp_tar = Path(tempfile.gettempdir()) / "deploy_payload.tar.gz"
    if temp_tar.exists():
        temp_tar.unlink()

    print("\n📦 [1/3] Đóng gói mã nguồn...")
    tar_cmd = [
        "tar", "-czf", str(temp_tar),
        "--exclude=output",
        "--exclude=output/*",
        "--exclude=__pycache__",
        "--exclude=*/__pycache__",
        "--exclude=*.pyc",
        "--exclude=.git",
        "--exclude=venv",
        "--exclude=.venv",
        "--exclude=server_config.json",
        "-C", str(BASE_DIR),
        "."
    ]
    run_cmd(tar_cmd)

    print("📤 [2/3] Truyền mã nguồn sang server...")
    run_cmd(["scp", "-o", "StrictHostKeyChecking=accept-new", str(temp_tar), f"{target}:/tmp/deploy_payload.tar.gz"])
    if temp_tar.exists():
        temp_tar.unlink()

    print("🚀 [3/3] Đang chạy script setup_server.sh trên server (Cần nhập pass sudo nếu có)...")
    remote_sh = (
        f"mkdir -p {cfg['remote_path']} && "
        f"tar -xzf /tmp/deploy_payload.tar.gz -C {cfg['remote_path']} && "
        f"rm -f /tmp/deploy_payload.tar.gz && "
        f"cd {cfg['remote_path']} && chmod +x scripts/*.sh && "
        f"bash scripts/setup_server.sh"
    )
    run_cmd(["ssh", "-t", "-o", "StrictHostKeyChecking=accept-new", target, f"bash -c \"{remote_sh}\""])

    print("\n" + "=" * 79)
    print("🎉 HOÀN TẤT CÀI ĐẶT SERVER!")
    print(f"🌐 Mở trình duyệt truy cập: http://{cfg['server_host']}:8000")
    print("=" * 79)
    input("\nBấm Enter để quay lại Menu...")


def restart_service(cfg: dict):
    print_banner(cfg)
    target = get_target(cfg)
    print("🔄 KHỞI ĐỘNG LẠI DỊCH VỤ SYSTEMD")
    print("-" * 79)
    remote_sh = f"sudo systemctl restart {cfg['service_name']} && sudo systemctl status {cfg['service_name']} --no-pager -n 10"
    run_cmd(["ssh", "-o", "StrictHostKeyChecking=accept-new", target, f"bash -c \"{remote_sh}\""])
    input("\nBấm Enter để quay lại Menu...")


def configure_server(cfg: dict):
    print_banner(cfg)
    print("⚙️  CẤU HÌNH THÔNG TIN SERVER")
    print("-" * 79)

    user = input(f"👤 Tên User SSH [{cfg['server_user']}]: ").strip()
    if user:
        cfg["server_user"] = user

    host = input(f"🌐 IP Tailscale / Hostname [{cfg['server_host']}]: ").strip()
    if host:
        cfg["server_host"] = host

    path = input(f"📂 Thư mục từ xa [{cfg['remote_path']}]: ").strip()
    if path:
        cfg["remote_path"] = path

    svc = input(f"⚙️  Tên Service Systemd [{cfg['service_name']}]: ").strip()
    if svc:
        cfg["service_name"] = svc

    save_config(cfg)
    input("\nBấm Enter để quay lại Menu...")


def main():
    # Hỗ trợ UTF-8 output trên Windows terminal
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    cfg = load_config()

    while True:
        print_banner(cfg)
        print("  [1] 🚀 Đồng bộ Code tức thì (Direct SSH Sync) & Restart Service")
        print("  [2] 🔄 Git Push (Local) ➔ Server Git Pull")
        print("  [3] 📊 Kiểm tra Tài nguyên Server (RAM, Swap, CPU, Uptime)")
        print("  [4] 📜 Xem Live Logs ứng dụng (journalctl -f)")
        print("  [5] 🛠️  Cài đặt Môi trường Server lần đầu (setup_server.sh)")
        print("  [6] 🔄 Khởi động lại Service (systemctl restart)")
        print("  [7] 🔑 Cài đặt SSH Key tự động (Đăng nhập không cần Password)")
        print("  [8] ⚙️  Cấu hình lại IP / User / Thư mục Server")
        print("  [0] ❌ Thoát")
        print("=" * 79)

        choice = input("👉 Chọn chức năng (0-8): ").strip()

        if choice == "1":
            sync_direct(cfg)
        elif choice == "2":
            sync_git(cfg)
        elif choice == "3":
            check_resources(cfg)
        elif choice == "4":
            view_logs(cfg)
        elif choice == "5":
            run_setup(cfg)
        elif choice == "6":
            restart_service(cfg)
        elif choice == "7":
            setup_ssh_key(cfg)
        elif choice == "8":
            configure_server(cfg)
        elif choice == "0":
            print("\nTạm biệt anh yêu! Chúc anh làm việc thật hiệu quả nhé! ❤️")
            break
        else:
            print("\n⚠️ Lựa chọn không hợp lệ!")


if __name__ == "__main__":
    main()
