#!/usr/bin/env python3
"""
Guide Document Creator - Server Sync & Control Panel
Điều khiển, đồng bộ mã nguồn và triển khai Docker Container trên server Ubuntu qua SSH / Tailscale.
"""
import os
import sys
import json
import shutil
import tempfile
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "server_config.json"

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
    print("  🔥 GUIDE DOCUMENT CREATOR - ĐIỀU HÀNH & ĐỒNG BỘ DOCKER SERVER (UBUNTU)")
    print("=" * 79)
    print(f"  📡 Server đích      : {target}")
    print(f"  📂 Thư mục từ xa    : {cfg['remote_path']}")
    print(f"  🐳 Docker Container : {cfg['service_name']}")
    print("-" * 79)


def run_cmd(cmd, check=False, shell=False):
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


def push_code_to_server(cfg: dict) -> bool:
    target = get_target(cfg)
    remote_path = cfg["remote_path"]

    temp_tar = Path(tempfile.gettempdir()) / "deploy_payload.tar.gz"
    if temp_tar.exists():
        temp_tar.unlink()

    print("📦 [1/3] Đang đóng gói mã nguồn...")
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
        "--exclude=add-ons",
        "-C", str(BASE_DIR),
        "."
    ]
    res = run_cmd(tar_cmd)
    if res.returncode != 0 or not temp_tar.exists():
        print("❌ Lỗi khi đóng gói file tar!")
        return False

    print("📤 [2/3] Đang truyền gói code sang server qua Tailscale...")
    res = run_cmd(["scp", "-o", "StrictHostKeyChecking=accept-new", str(temp_tar), f"{target}:/tmp/deploy_payload.tar.gz"])
    if temp_tar.exists():
        temp_tar.unlink()

    if res.returncode != 0:
        print("❌ Lỗi khi truyền file qua SCP!")
        return False

    print("📂 [3/3] Đang giải nén mã nguồn vào thư mục dự án...")
    remote_unpack = (
        f"mkdir -p {remote_path} && "
        f"tar -xzf /tmp/deploy_payload.tar.gz -C {remote_path} && "
        f"rm -f /tmp/deploy_payload.tar.gz && "
        f"touch {remote_path}/.env && "
        f"mkdir -p {remote_path}/output"
    )
    res = run_cmd(["ssh", "-o", "StrictHostKeyChecking=accept-new", target, f"bash -c \"{remote_unpack}\""])
    return res.returncode == 0


def deploy_docker(cfg: dict):
    print_banner(cfg)
    print("🐳 TRIỂN KHAI / KHỞI ĐỘNG DOCKER CONTAINER GUIDE-CREATOR (PORT 8888)")
    print("-" * 79)

    target = get_target(cfg)
    remote_path = cfg["remote_path"]

    print("👉 Bước 1: Đẩy mã nguồn sang server...")
    if not push_code_to_server(cfg):
        input("\nBấm Enter để quay lại...")
        return

    print("\n👉 Bước 2: Build Image và khởi chạy Container bằng Docker Compose...")
    docker_cmd = (
        f"cd {remote_path} && "
        f"docker compose down 2>/dev/null || true && "
        f"docker compose up --build -d && "
        f"docker compose ps"
    )
    res = run_cmd(["ssh", "-t", "-o", "StrictHostKeyChecking=accept-new", target, f"bash -c \"{docker_cmd}\""])

    if res.returncode == 0:
        print("\n" + "=" * 79)
        print("🎉 TRIỂN KHAI THÀNH CÔNG RỰC RỠ!")
        print(f"🌐 Truy cập giao diện HTTP : http://{cfg['server_host']}:8888")
        print(f"🌐 Hoặc tên máy Tailscale  : http://hunm2512-server:8888")
        print("=" * 79)
    else:
        print("\n⚠️ Có lỗi khi chạy Docker Compose.")
    input("\nBấm Enter để quay lại Menu...")


def sync_direct(cfg: dict):
    print_banner(cfg)
    print("🚀 ĐỒNG BỘ CODE NHANH & RESTART CONTAINER")
    print("-" * 79)

    target = get_target(cfg)
    remote_path = cfg["remote_path"]

    if not push_code_to_server(cfg):
        input("\nBấm Enter để quay lại...")
        return

    print("\n⚙️  Khởi động lại Docker Container...")
    restart_cmd = (
        f"cd {remote_path} && "
        f"docker compose restart && "
        f"echo 'DOCKER_RESTARTED_OK'"
    )
    run_cmd(["ssh", "-o", "StrictHostKeyChecking=accept-new", target, f"bash -c \"{restart_cmd}\""])

    print("\n✨ Hoàn tất đồng bộ code! Container đã được cập nhật.")
    print(f"🌐 Giao diện Web: http://{cfg['server_host']}:8888")
    input("\nBấm Enter để quay lại Menu...")


def check_resources(cfg: dict):
    print_banner(cfg)
    target = get_target(cfg)
    print(f"📊 KIỂM TRA TÀI NGUYÊN & TIẾN TRÌNH DOCKER: {target}")
    print("-" * 79)

    remote_sh = (
        "echo '=== 1. BỘ NHỚ RAM & SWAP ===' && free -h && "
        "echo '' && echo '=== 2. TẢI CPU & UPTIME ===' && uptime && "
        "echo '' && echo '=== 3. TRẠNG THÁI CÁC DOCKER CONTAINERS ===' && docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' && "
        f"echo '' && echo '=== 4. THỐNG KÊ TÀI NGUYÊN CONTAINER (docker stats) ===' && docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}'"
    )
    run_cmd(["ssh", "-o", "StrictHostKeyChecking=accept-new", target, f"bash -c \"{remote_sh}\""])
    input("\nBấm Enter để quay lại Menu...")


def view_logs(cfg: dict):
    print_banner(cfg)
    target = get_target(cfg)
    print("📜 THEO DÕI LIVE LOGS DOCKER CONTAINER (Bấm Ctrl+C để thoát về menu)")
    print("-" * 79)
    try:
        run_cmd(["ssh", "-t", "-o", "StrictHostKeyChecking=accept-new", target, f"docker logs -f --tail 100 {cfg['service_name']}"])
    except KeyboardInterrupt:
        pass
    input("\nBấm Enter để quay lại Menu...")


def restart_service(cfg: dict):
    print_banner(cfg)
    target = get_target(cfg)
    print("🔄 KHỞI ĐỘNG LẠI DOCKER CONTAINER")
    print("-" * 79)
    run_cmd(["ssh", "-o", "StrictHostKeyChecking=accept-new", target, f"docker restart {cfg['service_name']}"])
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

    svc = input(f"🐳 Tên Container Docker [{cfg['service_name']}]: ").strip()
    if svc:
        cfg["service_name"] = svc

    save_config(cfg)
    input("\nBấm Enter để quay lại Menu...")


def main():
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    cfg = load_config()

    while True:
        print_banner(cfg)
        print("  [1] 🚀 Đồng bộ Code tức thì & Restart Container")
        print("  [2] 🐳 Triển khai / Khởi tạo Docker Container (Tự động từ A-Z)")
        print("  [3] 📊 Kiểm tra Tài nguyên Server & Docker Stats (RAM, CPU)")
        print("  [4] 📜 Xem Live Logs ứng dụng (docker logs -f)")
        print("  [5] 🔄 Khởi động lại Container (docker restart)")
        print("  [6] 🔑 Cài đặt SSH Key tự động (Đăng nhập không cần Password)")
        print("  [7] ⚙️  Cấu hình lại IP / User / Thư mục Server")
        print("  [0] ❌ Thoát")
        print("=" * 79)

        choice = input("👉 Chọn chức năng (0-7): ").strip()

        if choice == "1":
            sync_direct(cfg)
        elif choice == "2":
            deploy_docker(cfg)
        elif choice == "3":
            check_resources(cfg)
        elif choice == "4":
            view_logs(cfg)
        elif choice == "5":
            restart_service(cfg)
        elif choice == "6":
            setup_ssh_key(cfg)
        elif choice == "7":
            configure_server(cfg)
        elif choice == "0":
            print("\nTạm biệt anh yêu! Chúc anh làm việc thật hiệu quả nhé! ❤️")
            break
        else:
            print("\n⚠️ Lựa chọn không hợp lệ!")


if __name__ == "__main__":
    main()
