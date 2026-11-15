#!/bin/bash
# packaging/build_mac.sh  Builds Zephyr.app and Zephyr.dmg. Mac only. Owner: Swayam.
#
#   bash packaging/build_mac.sh        (from the repo, with the project venv active)
#
# Output in packaging/dist/:
#   Zephyr.app   double-click to run, or drag to Applications
#   Zephyr.dmg   the file to share (GitHub release)
#
# Optional: ZEPHYR_SIGN_ID="Zephyr Dev" signs with that certificate, so macOS keeps the
# Camera and Accessibility permissions across rebuilds (see packaging/README.md).
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "$(uname)" != "Darwin" ]]; then
    echo "Zephyr.app is Mac only and has to be built on a Mac." >&2
    exit 1
fi

PY="${PYTHON:-python3}"
"$PY" - <<'EOF'
import sys
if sys.version_info[:2] not in ((3, 11), (3, 12)):
    sys.exit(f"Use Python 3.11 or 3.12 (this is {sys.version.split()[0]}). Activate the project venv first.")
EOF

OUT=packaging/dist
WORK=packaging/build
APP_HOME="$HOME/Library/Application Support/Zephyr"

echo "==> requirements and PyInstaller"
"$PY" -m pip install -q -r requirements.txt "pyinstaller>=6.10,<7"

echo "==> MediaPipe models"
"$PY" tools/get_models.py

echo "==> building (a few minutes)"
rm -rf "$WORK" "$OUT/Zephyr" "$OUT/Zephyr.app" "$OUT/Zephyr.dmg"
"$PY" -m PyInstaller --noconfirm --clean --distpath "$OUT" --workpath "$WORK" packaging/Zephyr.spec

echo "==> signing"
xattr -cr "$OUT/Zephyr.app"
codesign --force --deep --sign "${ZEPHYR_SIGN_ID:--}" "$OUT/Zephyr.app"
if codesign --verify --deep --strict "$OUT/Zephyr.app"; then
    echo "signature ok"
else
    echo "warning: signature check failed; the app may still open on this Mac" >&2
fi

echo "==> disk image"
STAGE="$(mktemp -d)"
ditto "$OUT/Zephyr.app" "$STAGE/Zephyr.app"
ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname Zephyr -srcfolder "$STAGE" -ov -format UDZO "$OUT/Zephyr.dmg"
rm -rf "$STAGE"

# keys for air writing and sync stay on this Mac next to the app's data, never inside the app
if [[ -f .env && ! -f "$APP_HOME/.env" ]]; then
    mkdir -p "$APP_HOME"
    cp .env "$APP_HOME/.env"
    chmod 600 "$APP_HOME/.env"
    echo "==> copied your .env to $APP_HOME (stays on this Mac, not in the app)"
fi

echo
echo "Done."
echo "  $OUT/Zephyr.app   double-click it, or drag it to Applications"
echo "  $OUT/Zephyr.dmg   share this one"
open -R "$OUT/Zephyr.app"
