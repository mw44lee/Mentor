#!/usr/bin/env bash
# Linux용 실행: 첫 실행이면 가상환경을 만들고 라이브러리를 설치한 뒤 앱을 켠다.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -q -r requirements.txt
fi
[ -f .env ] || cp .env.example .env
exec .venv/bin/python serve.py
