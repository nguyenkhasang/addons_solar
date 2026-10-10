#!/usr/bin/env bash
set -eu
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${ODOO_PROJECT_DIR:-$SCRIPT_DIR/../odoo-19.0}"
case "${1:-run}" in
    start|stop|restart) systemctl --user "$1" smartsolar-bms.service; echo "BMS: $1" ;;
    status) systemctl --user status smartsolar-bms.service --no-pager ;;
    logs) journalctl --user -u smartsolar-bms.service -n 50 -f ;;
    run|scan)
        if [[ "${1:-run}" == run ]]; then
            set -a
            source "${JK_BMS_ENV_FILE:-$PROJECT_DIR/.odoo-runtime/jk-bms.env}"
            set +a
        fi
        extra=()
        [[ "${1:-run}" == scan ]] && extra+=(--scan)
        exec "$PROJECT_DIR/venv/bin/python" "$SCRIPT_DIR/smartsolar-bms.py" --odoo-dir "$PROJECT_DIR" --config "$PROJECT_DIR/odoo.conf" "${extra[@]}" ;;
    *) echo 'Dùng: smartsolar-bms.sh start|stop|restart|status|logs|scan|run'; exit 2 ;;
esac
