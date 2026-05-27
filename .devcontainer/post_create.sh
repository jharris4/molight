#!/usr/bin/env bash
# Thin wrapper — real setup lives in scripts/setup
set -e
cd "$(dirname "$0")/.."
bash scripts/setup
