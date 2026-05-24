#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIGURATION="${ORAM_PLUGIN_BUILD_TYPE:-Debug}"
ARTEFACT_DIR="$ROOT_DIR/build/ORAMPlugin_artefacts/$CONFIGURATION"
AU_PATH="$HOME/Library/Audio/Plug-Ins/Components/ORAM.component"
VST3_PATH="$HOME/Library/Audio/Plug-Ins/VST3/ORAM.vst3"

"$ROOT_DIR/scripts/build.sh"
"$ROOT_DIR/scripts/install_debug_macos.sh"

codesign --verify --deep --strict --verbose=2 "$AU_PATH"
codesign --verify --deep --strict --verbose=2 "$VST3_PATH"

if [[ ! -f "$VST3_PATH/Contents/Resources/moduleinfo.json" ]]; then
  echo "missing VST3 moduleinfo.json" >&2
  exit 1
fi

if command -v auval >/dev/null 2>&1; then
  if ! auval -v aufx Oram Momo; then
    if [[ "${ORAM_PLUGIN_ALLOW_AUVAL_FAILURE:-0}" == "1" ]]; then
      echo "auval failed; continuing because ORAM_PLUGIN_ALLOW_AUVAL_FAILURE=1" >&2
    else
      exit 1
    fi
  fi
fi

find "$ARTEFACT_DIR" -maxdepth 4 \( -name "ORAM.vst3" -o -name "ORAM.component" -o -name "ORAM.app" \) -print
