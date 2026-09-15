#!/usr/bin/env bash
set -e

TARGET="$0"
while [ -L "$TARGET" ]; do
    DIR="$(cd -P "$(dirname "$TARGET")" >/dev/null 2>&1 && pwd)"
    TARGET="$(readlink "$TARGET")"
    [[ $TARGET != /* ]] && TARGET="$DIR/$TARGET"
done
SCRIPT_DIR="$(cd -P "$(dirname "$TARGET")" >/dev/null 2>&1 && pwd)"

exec "$SCRIPT_DIR/.venv/bin/vox" "$@"

