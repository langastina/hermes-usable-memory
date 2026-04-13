#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 /path/to/hermes-agent" >&2
  exit 1
fi

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/plugins/memory/usable"
TARGET_ROOT="$1"
TARGET_DIR="$TARGET_ROOT/plugins/memory/usable"

if [[ ! -d "$TARGET_ROOT" ]]; then
  echo "Hermes checkout not found: $TARGET_ROOT" >&2
  exit 1
fi

mkdir -p "$(dirname "$TARGET_DIR")"
rm -rf "$TARGET_DIR"
cp -R "$SOURCE_DIR" "$TARGET_DIR"

echo "Installed Usable memory provider to: $TARGET_DIR"
echo "Next:"
echo "  hermes config set memory.provider usable"
echo "  hermes memory setup"
