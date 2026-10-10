#!/usr/bin/env bash
set -eu
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${ODOO_PROJECT_DIR:-$SCRIPT_DIR/../odoo-19.0}"
cd "$PROJECT_DIR"
exec "$PROJECT_DIR/venv/bin/python" "$SCRIPT_DIR/smartsolar-listener.py" \
    --odoo-dir "$PROJECT_DIR" --config "$PROJECT_DIR/odoo.conf"
