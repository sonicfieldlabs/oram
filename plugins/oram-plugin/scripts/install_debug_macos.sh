#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIGURATION="${ORAM_PLUGIN_BUILD_TYPE:-Debug}"
ARTEFACT_DIR="$ROOT_DIR/build/ORAMPlugin_artefacts/$CONFIGURATION"

AU_SRC="$ARTEFACT_DIR/AU/ORAM.component"
VST3_SRC="$ARTEFACT_DIR/VST3/ORAM.vst3"
AU_DEST="$HOME/Library/Audio/Plug-Ins/Components/ORAM.component"
VST3_DEST="$HOME/Library/Audio/Plug-Ins/VST3/ORAM.vst3"

if [[ ! -d "$AU_SRC" || ! -d "$VST3_SRC" ]]; then
  "$ROOT_DIR/scripts/build.sh"
fi

mkdir -p "$(dirname "$AU_DEST")" "$(dirname "$VST3_DEST")"
rm -rf "$AU_DEST" "$VST3_DEST"
ditto "$AU_SRC" "$AU_DEST"
ditto "$VST3_SRC" "$VST3_DEST"
codesign --force --deep --sign - "$AU_DEST" "$VST3_DEST"
killall -9 AudioComponentRegistrar >/dev/null 2>&1 || true

echo "$AU_DEST"
echo "$VST3_DEST"
