# Shared environment and helpers for scripts/e2e*; source after cd to the repo root.
# The Playwright image must match the pinned @playwright/test package.
MOLIGHT_E2E_PLAYWRIGHT_VERSION="$(sed -n 's/.*"@playwright\/test": *"\([^"]*\)".*/\1/p' package.json)"
: "${MOLIGHT_E2E_PLAYWRIGHT_VERSION:?could not read @playwright/test from package.json}"
export MOLIGHT_E2E_PLAYWRIGHT_VERSION

# The Home Assistant release of the e2e image. CI checks it is the one the
# pinned test dependencies install, so both layers test the same release.
MOLIGHT_E2E_HA_VERSION=2026.8.2
export MOLIGHT_E2E_HA_IMAGE="${MOLIGHT_E2E_HA_IMAGE:-ghcr.io/home-assistant/home-assistant:$MOLIGHT_E2E_HA_VERSION}"

# Check every boot's logs. The runner cannot reach the container's output,
# which unlike home-assistant.log keeps them all, so save it where it can.
check_ha_logs() {
    echo "Checking the Home Assistant logs of every boot..."
    compose logs --no-color --no-log-prefix homeassistant \
        > "$MOLIGHT_E2E_CONFIG_DIR/e2e-container.log"
    compose run --rm runner python runner.py logs
}
