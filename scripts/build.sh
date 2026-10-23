#!/usr/bin/env bash
# Wiki Transporter 一键构建（Linux / macOS）
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
echo "== 安装依赖 =="
"$PY" -m pip install -r requirements.txt --quiet

echo "== 抓取 + 排版 + 导出 =="
"$PY" -m wiki_transporter all "$@"

echo "完成：out/ 目录下 docx / pdf / epub"
