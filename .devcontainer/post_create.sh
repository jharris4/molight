#!/usr/bin/env bash
set -e

echo "▶ Installing test dependencies (includes Home Assistant)..."
pip install --quiet -r requirements_test.txt

echo "▶ Setting up Home Assistant config directory..."
mkdir -p config

if [ ! -f config/configuration.yaml ]; then
    cat > config/configuration.yaml << 'EOF'
# Limer development configuration
default_config:

logger:
  default: info
  logs:
    custom_components.limer: debug
EOF
    echo "  Created config/configuration.yaml"
fi

# Symlink our custom component into the HA config dir so `hass -c config` picks it up
mkdir -p config/custom_components
if [ ! -L config/custom_components/limer ]; then
    ln -sf "$(pwd)/custom_components/limer" config/custom_components/limer
    echo "  Linked custom_components/limer → config/custom_components/limer"
fi

echo ""
echo "✅ Dev environment ready!"
echo ""
echo "  Run tests:              pytest"
echo "  Run tests with coverage: pytest --cov=custom_components/limer"
echo "  Lint:                   ruff check ."
echo "  Start Home Assistant:   hass -c config"
echo "  HA UI (when running):   http://localhost:8123"
echo ""
