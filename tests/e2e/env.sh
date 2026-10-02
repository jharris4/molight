# shellcheck shell=bash
# Shared environment and helpers for scripts/e2e*; source after cd to the repo root.
# The Playwright image must match the pinned @playwright/test package.
MOLIGHT_E2E_PLAYWRIGHT_VERSION="$(sed -n 's/.*"@playwright\/test": *"\([^"]*\)".*/\1/p' package.json)"
: "${MOLIGHT_E2E_PLAYWRIGHT_VERSION:?could not read @playwright/test from package.json}"
export MOLIGHT_E2E_PLAYWRIGHT_VERSION

# The Home Assistant release of the e2e image. CI checks it is the one the
# pinned test dependencies install, so both layers test the same release.
MOLIGHT_E2E_HA_VERSION=2026.9.4
# "floor" picks the minimum release hacs.json declares.
if [ "${MOLIGHT_E2E_HA_IMAGE:-}" = floor ]; then
    MOLIGHT_E2E_HA_VERSION="$(sed -n 's/.*"homeassistant": *"\([^"]*\)".*/\1/p' hacs.json)"
    : "${MOLIGHT_E2E_HA_VERSION:?could not read homeassistant from hacs.json}"
    MOLIGHT_E2E_HA_IMAGE=""
fi
export MOLIGHT_E2E_HA_IMAGE="${MOLIGHT_E2E_HA_IMAGE:-ghcr.io/home-assistant/home-assistant:$MOLIGHT_E2E_HA_VERSION}"

export MOLIGHT_E2E_RUNNER_IMAGE=python:3.14-slim

# Delete directories the containers wrote to. Docker on a Linux host leaves
# their files owned by root, so what rm cannot delete goes through a container.
remove_dirs() {
    rm -rf "$@" 2>/dev/null && return
    local dir
    for dir in "$@"; do
        [ -d "$dir" ] || continue
        docker run --rm --network none --volume "$dir:/remove" \
            "$MOLIGHT_E2E_RUNNER_IMAGE" find /remove -mindepth 1 -delete
    done
    rm -rf "$@"
}

# Check every boot's logs; the argument is how many boots the lane made. The
# runner cannot reach the container's output, which unlike home-assistant.log
# keeps them all, so save it where it can.
check_ha_logs() {
    echo "Checking the Home Assistant logs of every boot..."
    compose logs --no-color --no-log-prefix homeassistant \
        > "$MOLIGHT_E2E_CONFIG_DIR/e2e-container.log"
    compose run --rm runner python runner.py logs "${1:?check_ha_logs needs the boot count of the lane}"
}
