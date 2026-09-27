#!/usr/bin/env bash
# Regenerate requirements.lock from uv.lock, or check that it is current.
#
#   scripts/lock.sh          rewrite requirements.lock (run after `uv lock`)
#   scripts/lock.sh --check  exit 1 if uv.lock or requirements.lock is out of date, or if a locked
#                            package has no wheel for a platform Vox installs on
#
# install.sh and the .deb install exactly these pinned, hash-checked packages, plus the locked
# setuptools that builds Vox itself. evdev is left out: only pynput's uinput backend imports it,
# Vox never selects that backend, and evdev ships no wheels, so it would need a C compiler.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

render() {
    printf '%s\n' \
        '# Generated from uv.lock by scripts/lock.sh; do not edit. Install with:' \
        '#   pip install --require-hashes --no-deps -r requirements.lock'
    uv export --frozen --no-dev --group build --no-emit-project --no-emit-package evdev \
        --format requirements.txt --no-header
}

# Installing must never need a compiler, so every package needs a wheel for each supported
# platform: Ubuntu 24.04 and later (glibc 2.39) and macOS, with Python 3.12 or 3.13
check_wheels() {
    local platform python
    for platform in x86_64-manylinux_2_39 aarch64-manylinux_2_39 aarch64-apple-darwin x86_64-apple-darwin; do
        for python in 3.12 3.13; do
            if ! uv pip compile --quiet --no-header --no-deps --only-binary :all: \
                --python-platform "$platform" --python-version "$python" requirements.lock >/dev/null; then
                echo "a package in requirements.lock has no wheel for $platform and Python $python" >&2
                return 1
            fi
        done
    done
}

case "${1:-}" in
"")
    render >requirements.lock
    ;;
--check)
    uv lock --check
    if ! diff -u requirements.lock <(render); then
        echo "requirements.lock is out of date: run scripts/lock.sh" >&2
        exit 1
    fi
    check_wheels
    ;;
*)
    sed -n '2,6s/^# \{0,1\}//p' "$0"
    exit 2
    ;;
esac
