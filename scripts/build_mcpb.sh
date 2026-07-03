#!/usr/bin/env bash
# Build Magenda.mcpb — a one-click MCP Bundle for Claude Desktop.
#
# Prerequisites (build machine only):
#   pip install pyinstaller
#   Node.js (npx is used to run the mcpb CLI; `npm install -g @anthropic-ai/mcpb`
#   also works and is picked up automatically if present)
#
# Usage:
#   ./scripts/build_mcpb.sh [--sign "Developer ID Application: Your Name (TEAMID)"]
#
# Output: dist/Magenda-<version>.mcpb
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [ -x ".venv/bin/pyinstaller" ]; then
  PYTHON=".venv/bin/python"
  PYINSTALLER=".venv/bin/pyinstaller"
elif command -v pyinstaller &>/dev/null; then
  PYTHON="$(command -v python3)"
  PYINSTALLER="$(command -v pyinstaller)"
else
  echo "PyInstaller not found — installing into .venv…"
  .venv/bin/pip install --quiet pyinstaller
  PYTHON=".venv/bin/python"
  PYINSTALLER=".venv/bin/pyinstaller"
fi

if command -v mcpb &>/dev/null; then
  MCPB=(mcpb)
else
  MCPB=(npx --yes "@anthropic-ai/mcpb@latest")
fi

VERSION=$("$PYTHON" -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); print(d['project']['version'])")
APP_NAME="Magenda"
BUNDLE_DIR="dist/${APP_NAME}"
STAGE="dist/mcpb-stage"
MCPB_PATH="dist/${APP_NAME}-${VERSION}.mcpb"

SIGN_IDENTITY="${SIGN_IDENTITY:-}"
for arg in "$@"; do
  case "$arg" in
    --sign) SIGN_IDENTITY="${2:-}"; shift 2 ;;
  esac
done

echo "=== Building ${APP_NAME} ${VERSION} ==="

# ── 1. Build the onedir executable bundle ─────────────────────────────────
echo "→ Running PyInstaller…"
"$PYINSTALLER" --noconfirm Magenda.spec

BIN_PATH="${BUNDLE_DIR}/magenda"

# ── 2. Optional code signing of the executable ────────────────────────────
if [ -n "$SIGN_IDENTITY" ]; then
  echo "→ Code-signing with: ${SIGN_IDENTITY}"
  codesign --deep --force --options runtime \
    --sign "$SIGN_IDENTITY" \
    --entitlements scripts/entitlements.plist \
    "$BIN_PATH"
fi

# ── 3. Stage the .mcpb package ────────────────────────────────────────────
echo "→ Staging package…"
rm -rf "$STAGE"
mkdir -p "$STAGE/server"
cp -R "${BUNDLE_DIR}/." "$STAGE/server/"

ICON_SRC="src/magenda/resources/icon_512.png"
"$PYTHON" - "$STAGE/manifest.json" "$VERSION" "$ICON_SRC" <<'PYEOF'
import json
import os
import sys

out_path, version, icon_src = sys.argv[1], sys.argv[2], sys.argv[3]

with open("manifest.json") as f:
    manifest = json.load(f)
manifest["version"] = version
if os.path.isfile(icon_src):
    manifest["icon"] = "icon.png"

with open(out_path, "w") as f:
    json.dump(manifest, f, indent=2)
    f.write("\n")
PYEOF

if [ -f "$ICON_SRC" ]; then
  cp "$ICON_SRC" "$STAGE/icon.png"
fi

# ── 4. Pack ────────────────────────────────────────────────────────────────
echo "→ Packing .mcpb…"
rm -f "$MCPB_PATH"
"${MCPB[@]}" pack "$STAGE" "$MCPB_PATH"

echo ""
echo "✓ Done: ${MCPB_PATH}"
echo "  Size: $(du -sh "${MCPB_PATH}" | cut -f1)"
echo ""
echo "Requires LibreOffice for PDF rendering (not bundled):"
echo "  brew install --cask libreoffice"
echo ""
echo "Install by double-clicking the .mcpb, or via Claude Desktop → Settings → Extensions."
