#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIGURATION="${ORAM_PLUGIN_BUILD_TYPE:-Release}"
BUILD_DIR="$ROOT_DIR/build"
ARTEFACT_DIR="$BUILD_DIR/ORAMPlugin_artefacts/$CONFIGURATION"
DIST_DIR="$ROOT_DIR/dist/macos"
SIGN_IDENTITY="${ORAM_PLUGIN_SIGN_IDENTITY:--}"

ORAM_PLUGIN_BUILD_TYPE="$CONFIGURATION" "$ROOT_DIR/scripts/build.sh"

mkdir -p "$DIST_DIR"
rm -f \
  "$DIST_DIR/ORAM-AU-$CONFIGURATION.zip" \
  "$DIST_DIR/ORAM-VST3-$CONFIGURATION.zip" \
  "$DIST_DIR/ORAM-Standalone-$CONFIGURATION.zip" \
  "$DIST_DIR/checksums.txt"

for bundle in "$ARTEFACT_DIR/AU/ORAM.component" "$ARTEFACT_DIR/VST3/ORAM.vst3" "$ARTEFACT_DIR/Standalone/ORAM.app"; do
  if [[ -d "$bundle" ]]; then
    codesign --force --deep --sign "$SIGN_IDENTITY" "$bundle"
  fi
done

ditto -c -k --keepParent "$ARTEFACT_DIR/AU/ORAM.component" "$DIST_DIR/ORAM-AU-$CONFIGURATION.zip"
ditto -c -k --keepParent "$ARTEFACT_DIR/VST3/ORAM.vst3" "$DIST_DIR/ORAM-VST3-$CONFIGURATION.zip"
ditto -c -k --keepParent "$ARTEFACT_DIR/Standalone/ORAM.app" "$DIST_DIR/ORAM-Standalone-$CONFIGURATION.zip"

(
  cd "$DIST_DIR"
  shasum -a 256 ORAM-AU-"$CONFIGURATION".zip ORAM-VST3-"$CONFIGURATION".zip ORAM-Standalone-"$CONFIGURATION".zip > checksums.txt
)

if [[ -n "${ORAM_PLUGIN_NOTARY_PROFILE:-}" ]]; then
  xcrun notarytool submit "$DIST_DIR/ORAM-AU-$CONFIGURATION.zip" --keychain-profile "$ORAM_PLUGIN_NOTARY_PROFILE" --wait
  xcrun notarytool submit "$DIST_DIR/ORAM-VST3-$CONFIGURATION.zip" --keychain-profile "$ORAM_PLUGIN_NOTARY_PROFILE" --wait
  xcrun notarytool submit "$DIST_DIR/ORAM-Standalone-$CONFIGURATION.zip" --keychain-profile "$ORAM_PLUGIN_NOTARY_PROFILE" --wait
fi

echo "$DIST_DIR"
