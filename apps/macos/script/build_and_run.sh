#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT_DIR/../.." && pwd)"
APP_NAME="ORAM"
BUNDLE_ID="wtf.momoto.oram"
BUILD_CONFIGURATION="${ORAM_BUILD_CONFIGURATION:-release}"
DIST_DIR="$ROOT_DIR/dist"
APP_DIR="$DIST_DIR/$APP_NAME.app"
CONTENTS_DIR="$APP_DIR/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"
PYTHON_RESOURCE_DIR="$RESOURCES_DIR/oram-python"
BIN_RESOURCE_DIR="$RESOURCES_DIR/bin"

cd "$ROOT_DIR"

export HOME="$ROOT_DIR/.build/home"
export SWIFTPM_CACHE_PATH="$ROOT_DIR/.build/swiftpm-cache"
export CLANG_MODULE_CACHE_PATH="$ROOT_DIR/.build/clang-module-cache"
mkdir -p "$HOME" "$SWIFTPM_CACHE_PATH" "$CLANG_MODULE_CACHE_PATH"

if pgrep -x "$APP_NAME" >/dev/null 2>&1; then
  pkill -x "$APP_NAME" || true
fi

swift build -c "$BUILD_CONFIGURATION"
BIN_PATH="$(swift build -c "$BUILD_CONFIGURATION" --show-bin-path)/$APP_NAME"

rm -rf "$APP_DIR"
mkdir -p "$MACOS_DIR" "$PYTHON_RESOURCE_DIR" "$BIN_RESOURCE_DIR"
cp "$BIN_PATH" "$MACOS_DIR/$APP_NAME"
strip -x "$MACOS_DIR/$APP_NAME" 2>/dev/null || true
if UV_BIN="$(command -v uv 2>/dev/null)"; then
  cp "$UV_BIN" "$BIN_RESOURCE_DIR/uv"
  strip -x "$BIN_RESOURCE_DIR/uv" 2>/dev/null || true
  perl -0pi -e '$p = "/" . "Users" . "/" . "brew"; s#$p#/usr/uv_src#g' "$BIN_RESOURCE_DIR/uv" 2>/dev/null || true
  codesign --force --sign - "$BIN_RESOURCE_DIR/uv" >/dev/null 2>&1 || true
fi
cp "$REPO_ROOT/pyproject.toml" "$PYTHON_RESOURCE_DIR/pyproject.toml"
cp "$REPO_ROOT/README.md" "$PYTHON_RESOURCE_DIR/README.md"
cp "$REPO_ROOT/LICENSE" "$PYTHON_RESOURCE_DIR/LICENSE"
if [[ -f "$REPO_ROOT/uv.lock" ]]; then
  cp "$REPO_ROOT/uv.lock" "$PYTHON_RESOURCE_DIR/uv.lock"
fi
if [[ -f "$REPO_ROOT/engines.yaml" ]]; then
  cp "$REPO_ROOT/engines.yaml" "$PYTHON_RESOURCE_DIR/engines.yaml"
fi
if [[ -f "$ROOT_DIR/Assets/logo-oram.png" ]]; then
  cp "$ROOT_DIR/Assets/logo-oram.png" "$RESOURCES_DIR/logo-oram.png"
fi
if [[ -f "$ROOT_DIR/Assets/AppIcon.icns" ]]; then
  cp "$ROOT_DIR/Assets/AppIcon.icns" "$RESOURCES_DIR/AppIcon.icns"
fi
rm -rf "$PYTHON_RESOURCE_DIR/src"
cp -R "$REPO_ROOT/src" "$PYTHON_RESOURCE_DIR/src"
find "$PYTHON_RESOURCE_DIR/src" \( -name "__pycache__" -o -name "*.pyc" -o -name ".DS_Store" \) -print0 | xargs -0 rm -rf

cat > "$CONTENTS_DIR/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>$APP_NAME</string>
  <key>CFBundleIdentifier</key>
  <string>$BUNDLE_ID</string>
  <key>CFBundleName</key>
  <string>$APP_NAME</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>0.1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>NSPrincipalClass</key>
  <string>NSApplication</string>
  <key>NSMicrophoneUsageDescription</key>
  <string>ORAM records local audio into layers when you press record.</string>
</dict>
</plist>
PLIST

if [[ "${1:-}" == "--verify" ]]; then
  /usr/bin/open -n "$APP_DIR"
  sleep 2
  pgrep -x "$APP_NAME" >/dev/null
  echo "$APP_NAME launched"
elif [[ "${1:-}" == "--no-open" ]]; then
  echo "$APP_DIR"
else
  /usr/bin/open -n "$APP_DIR"
  echo "$APP_DIR"
fi
