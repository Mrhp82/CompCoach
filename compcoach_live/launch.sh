#!/usr/bin/env bash
set -euo pipefail

# Keep the caller's working directory: Streamlit reads .streamlit there.
app_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python -m streamlit run "$app_directory/app.py" --server.address 0.0.0.0 --server.port 8501 "$@"
