#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$ROOT_DIR/build"

cmake -S "$ROOT_DIR" -B "$BUILD_DIR" -DCMAKE_BUILD_TYPE="${ORAM_PLUGIN_BUILD_TYPE:-Debug}"
cmake --build "$BUILD_DIR" --target ORAMPlugin_All --config "${ORAM_PLUGIN_BUILD_TYPE:-Debug}"

find "$BUILD_DIR/ORAMPlugin_artefacts/${ORAM_PLUGIN_BUILD_TYPE:-Debug}" -maxdepth 4 \
  \( -name "ORAM.vst3" -o -name "ORAM.component" -o -name "ORAM.app" \) -print
