#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "[1/4] compileall"
python -m compileall .

echo "[2/4] django check"
python manage.py check

echo "[3/4] migrations drift check"
python manage.py makemigrations --check --dry-run

echo "[4/4] tests (if any)"
python manage.py test
