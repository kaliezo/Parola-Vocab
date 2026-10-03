#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$HOME/.local/bin:$PATH"
python_bin="python3"
if [[ -x "$project_dir/.venv/bin/python" ]]; then
    python_bin="$project_dir/.venv/bin/python"
fi
if ! "$python_bin" -c 'import PySide6, sqlite3' >/dev/null 2>&1; then
    echo "Vocabulary needs PySide6. From this folder run:" >&2
    echo "  python3 -m venv .venv" >&2
    echo "  .venv/bin/python -m pip install -r requirements.lock" >&2
    exit 1
fi
exec "$python_bin" "$project_dir/qt_app.py"
