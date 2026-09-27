#!/usr/bin/env bash
# Print the CHANGELOG.md section for a version, which becomes that release's notes.
#
#   scripts/changelog-notes.sh 1.0.0
set -euo pipefail
version=${1:?usage: scripts/changelog-notes.sh VERSION}
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# The lines between "## [VERSION]" and the next "## [" heading, without link definitions
# or leading blank lines
notes=$(awk -v heading="## [$version]" '
    /^## \[/ { if (found) exit; found = index($0, heading) == 1; next }
    !found || /^\[[^]]+\]: / { next }
    started || NF { started = 1; print }
' CHANGELOG.md)
if [ -z "$notes" ]; then
    echo "CHANGELOG.md has no section for $version" >&2
    exit 1
fi
printf '%s\n' "$notes"
