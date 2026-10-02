#!/usr/bin/env bash

set -u

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${ODOO_PROJECT_DIR:-$SCRIPT_DIR/../odoo-19.0}"
PROJECT_DIR="$(cd -- "$PROJECT_DIR" && pwd)"
PYTHON="$PROJECT_DIR/venv/bin/python"
ODOO_BIN="$PROJECT_DIR/odoo-bin"
CONFIG="$PROJECT_DIR/odoo.conf"
RUNTIME_DIR="$PROJECT_DIR/.odoo-runtime"
LOG_FILE="$RUNTIME_DIR/odoo.log"
SERVICE_NAME="smartsolar-odoo.service"

mkdir -p "$RUNTIME_DIR"

read_pid() {
    systemctl --user show "$SERVICE_NAME" --property=MainPID --value
}

is_running() {
    local pid
    pid="$(read_pid)"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    kill -0 "$pid" 2>/dev/null || return 1

    # Avoid signaling a reused PID that no longer belongs to this project.
    tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -Fq "$ODOO_BIN"
}

start() {
    echo "Đang khởi động Odoo..."
    if ! systemctl --user start "$SERVICE_NAME"; then
        echo "Khởi động Odoo thất bại. Xem: journalctl --user -u $SERVICE_NAME -n 50" >&2
        return 1
    fi
    confirm_running "khởi động"
}

stop() {
    echo "Đang dừng Odoo..."
    if ! systemctl --user stop "$SERVICE_NAME"; then
        echo "Không thể dừng Odoo." >&2
        return 1
    fi
    echo "Odoo đã dừng."
}

confirm_running() {
    sleep 2
    if systemctl --user is-active --quiet "$SERVICE_NAME" && is_running; then
        echo "Odoo đã $1 thành công (PID $(read_pid))."
        echo "Log: $LOG_FILE"
    else
        echo "Odoo chưa chạy ổn định. Xem: journalctl --user -u $SERVICE_NAME -n 50" >&2
        return 1
    fi
}

restart() {
    echo "Đang restart Odoo..."
    if ! systemctl --user restart "$SERVICE_NAME"; then
        echo "Restart Odoo thất bại. Xem: journalctl --user -u $SERVICE_NAME -n 50" >&2
        return 1
    fi
    confirm_running "restart"
}

status() {
    local pid http_port os_name memory disk load uptime_text external_pids active_state sub_state
    active_state="$(systemctl --user show "$SERVICE_NAME" -p ActiveState --value)"
    sub_state="$(systemctl --user show "$SERVICE_NAME" -p SubState --value)"
    http_port="$(awk -F= '/^[[:space:]]*http_port[[:space:]]*=/ {gsub(/[[:space:]]/, "", $2); print $2; exit}' "$CONFIG")"
    http_port="${http_port:-8069}"
    os_name="$(. /etc/os-release 2>/dev/null && printf '%s' "${PRETTY_NAME:-Linux}")"
    memory="$(free -h | awk '/^Mem:/ {printf "%s / %s", $3, $2}')"
    disk="$(df -h --output=used,size,pcent "$PROJECT_DIR" | awk 'NR == 2 {printf "%s / %s (%s)", $1, $2, $3}')"
    load="$(awk '{print $1", "$2", "$3}' /proc/loadavg)"
    uptime_text="$(uptime -p 2>/dev/null | sed 's/^up //' || true)"

    echo "===== Hệ thống ====="
    printf '%-14s %s\n' "Máy chủ:" "$(hostname)"
    printf '%-14s %s\n' "Hệ điều hành:" "$os_name"
    printf '%-14s %s\n' "Uptime:" "${uptime_text:-không xác định}"
    printf '%-14s %s\n' "Load average:" "$load"
    printf '%-14s %s\n' "RAM:" "$memory"
    printf '%-14s %s\n' "Ổ đĩa:" "$disk"
    echo
    echo "===== Odoo ====="
    printf '%-14s %s\n' "Systemd:" "$(systemctl --user is-active "$SERVICE_NAME")"
    printf '%-14s %s\n' "Tự khởi chạy:" "$(systemctl --user is-enabled "$SERVICE_NAME")"
    printf '%-14s %s\n' "Boot linger:" "$(loginctl show-user "$(id -un)" -p Linger --value)"
    printf '%-14s %s\n' "Auto restart:" "$(systemctl --user show "$SERVICE_NAME" -p Restart --value)"
    printf '%-14s %s\n' "Số restart:" "$(systemctl --user show "$SERVICE_NAME" -p NRestarts --value)"

    if is_running; then
        pid="$(read_pid)"
        printf '%-14s %s\n' "Trạng thái:" "ĐANG CHẠY"
        printf '%-14s %s\n' "PID:" "$pid"
        ps -p "$pid" -o etime=,%cpu=,%mem=,rss= | awk '{printf "%-14s %s\n%-14s %s%%\n%-14s %s%%\n%-14s %.1f MB\n", "Thời gian chạy:", $1, "CPU:", $2, "RAM tiến trình:", $3, "Bộ nhớ RSS:", $4 / 1024}'
        printf '%-14s %s\n' "Địa chỉ:" "http://localhost:$http_port"
        printf '%-14s %s\n' "Cấu hình:" "$CONFIG"
        printf '%-14s %s\n' "Log:" "$LOG_FILE"
    else
        case "$active_state/$sub_state" in
            activating/auto-restart) printf '%-14s %s\n' "Trạng thái:" "ĐANG TỰ RESTART" ;;
            activating/*) printf '%-14s %s\n' "Trạng thái:" "ĐANG KHỞI ĐỘNG" ;;
            deactivating/*) printf '%-14s %s\n' "Trạng thái:" "ĐANG DỪNG" ;;
            failed/*) printf '%-14s %s\n' "Trạng thái:" "LỖI" ;;
            *) printf '%-14s %s\n' "Trạng thái:" "ĐÃ DỪNG" ;;
        esac
        external_pids="$(pgrep -f -- "$ODOO_BIN" | paste -sd, - 2>/dev/null || true)"
        if [[ -n "$external_pids" ]]; then
            printf '%-14s %s\n' "Lưu ý:" "Có tiến trình chạy ngoài script (PID $external_pids)"
        fi
        printf '%-14s %s\n' "Cấu hình:" "$CONFIG"
        printf '%-14s %s\n' "Log:" "$LOG_FILE"
        return 1
    fi
}

case "${1:-}" in
    run)
        cd "$PROJECT_DIR" || exit 1
        exec "$PYTHON" "$ODOO_BIN" -c "$CONFIG" --logfile="$LOG_FILE"
        ;;
    start) start ;;
    stop) stop ;;
    restart)
        restart
        ;;
    status) status ;;
    logs) tail -f "$LOG_FILE" ;;
    *)
        echo "Cách dùng: $0 {start|stop|restart|status|logs}" >&2
        exit 2
        ;;
esac
