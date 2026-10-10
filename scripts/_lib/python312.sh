# Discover installed Python only; no uv sync or managed interpreter download.
build_python() {
  local candidate
  for candidate in "$PWD/.venv/bin/python" python3.12 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -B -c 'import sys; sys.exit(sys.version_info[:2] != (3,12))' 2>/dev/null; then
      BUILD_PYTHON="$candidate"
      return
    fi
  done
  echo 'Python 3.12 is required. Install it explicitly; no build dependencies were installed.' >&2
  return 1
}
