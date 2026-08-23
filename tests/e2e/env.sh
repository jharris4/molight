# Shared environment for scripts/e2e*; source after cd to the repo root.
# The Playwright image must match the pinned @playwright/test package.
MOLIGHT_E2E_PLAYWRIGHT_VERSION="$(sed -n 's/.*"@playwright\/test": *"\([^"]*\)".*/\1/p' package.json)"
: "${MOLIGHT_E2E_PLAYWRIGHT_VERSION:?could not read @playwright/test from package.json}"
export MOLIGHT_E2E_PLAYWRIGHT_VERSION
