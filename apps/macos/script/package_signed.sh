#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT_DIR/../.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
APP_NAME="ORAM"
APP_DIR="$DIST_DIR/$APP_NAME.app"
DMG_PATH="$DIST_DIR/$APP_NAME-signed.dmg"
CHECKSUMS_PATH="$DIST_DIR/checksums-signed.txt"

if [[ -z "${ORAM_DEVELOPER_ID_APP:-}" ]]; then
  cat >&2 <<'EOF'
Set ORAM_DEVELOPER_ID_APP to your Developer ID Application identity.
Example:
  export ORAM_DEVELOPER_ID_APP="Developer ID Application: Your Name (TEAMID)"
EOF
  exit 2
fi

"$ROOT_DIR/script/build_and_run.sh" --no-open >/dev/null

if [[ -x "$APP_DIR/Contents/Resources/bin/uv" ]]; then
  codesign --force --timestamp --options runtime --sign "$ORAM_DEVELOPER_ID_APP" \
    "$APP_DIR/Contents/Resources/bin/uv"
fi

codesign --force --timestamp --options runtime --sign "$ORAM_DEVELOPER_ID_APP" "$APP_DIR"
codesign --verify --deep --strict --verbose=2 "$APP_DIR"

rm -f "$DMG_PATH"
hdiutil create -volname "$APP_NAME" -srcfolder "$APP_DIR" -ov -format UDZO "$DMG_PATH"
codesign --force --timestamp --sign "$ORAM_DEVELOPER_ID_APP" "$DMG_PATH"

if [[ -n "${ORAM_NOTARY_PROFILE:-}" ]]; then
  xcrun notarytool submit "$DMG_PATH" --keychain-profile "$ORAM_NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG_PATH"
  xcrun stapler validate "$DMG_PATH"
fi

(
  cd "$DIST_DIR"
  shasum -a 256 "$(basename "$DMG_PATH")" > "$(basename "$CHECKSUMS_PATH")"
)

if [[ "${ORAM_COPY_RELEASES:-0}" == "1" ]]; then
  RELEASE_DIR="$REPO_ROOT/releases/macos"
  mkdir -p "$RELEASE_DIR"
  cp "$DMG_PATH" "$RELEASE_DIR/$APP_NAME-signed.dmg"
  cp "$CHECKSUMS_PATH" "$RELEASE_DIR/checksums-signed.txt"
fi

echo "$DMG_PATH"
