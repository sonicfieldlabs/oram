#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT_DIR/../.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
APP_DIR="$DIST_DIR/ORAM.app"
DMG_PATH="$DIST_DIR/ORAM.dmg"
RELEASE_DIR="$REPO_ROOT/releases/macos"

"$ROOT_DIR/script/build_and_run.sh" --no-open >/dev/null

secret_file="$(find "$DIST_DIR" \( -name ".env" -o -name ".env.*" \) -print -quit)"
if [[ -n "$secret_file" ]]; then
  echo "secret-like file found in dist: $secret_file" >&2
  exit 1
fi

rm -f "$DMG_PATH"
hdiutil create -volname "ORAM" -srcfolder "$APP_DIR" -ov -format UDZO "$DMG_PATH"
(
  cd "$DIST_DIR"
  shasum -a 256 "ORAM.app/Contents/MacOS/ORAM" "ORAM.dmg" > "checksums.txt"
)

mkdir -p "$RELEASE_DIR"
cp "$DMG_PATH" "$RELEASE_DIR/ORAM.dmg"
(
  cd "$RELEASE_DIR"
  shasum -a 256 "ORAM.dmg" > "checksums.txt"
)

echo "$RELEASE_DIR/ORAM.dmg"
