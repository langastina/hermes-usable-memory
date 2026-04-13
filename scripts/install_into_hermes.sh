#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 /path/to/hermes-agent|/path/to/.hermes" >&2
  exit 1
fi

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/plugins/memory/usable"
TARGET_INPUT="${1/#\~/$HOME}"

if [[ ! -d "$TARGET_INPUT" ]]; then
  echo "Hermes path not found: $TARGET_INPUT" >&2
  exit 1
fi

if [[ -d "$TARGET_INPUT/hermes-agent/plugins/memory" ]]; then
  TARGET_ROOT="$TARGET_INPUT/hermes-agent"
elif [[ -d "$TARGET_INPUT/plugins/memory" ]]; then
  TARGET_ROOT="$TARGET_INPUT"
else
  echo "Could not find Hermes memory plugin directory under: $TARGET_INPUT" >&2
  echo "Expected one of:" >&2
  echo "  $TARGET_INPUT/plugins/memory" >&2
  echo "  $TARGET_INPUT/hermes-agent/plugins/memory" >&2
  exit 1
fi

TARGET_DIR="$TARGET_ROOT/plugins/memory/usable"
mkdir -p "$(dirname "$TARGET_DIR")"
rm -rf "$TARGET_DIR"
cp -R "$SOURCE_DIR" "$TARGET_DIR"

echo "Installed Usable memory provider to: $TARGET_DIR"
echo "Next:"
echo "  hermes config set memory.provider usable"
echo "  hermes memory setup"
