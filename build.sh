#!/usr/bin/env bash
set -e

pip install -r requirements.txt
playwright install chromium
# Note: 'playwright install-deps' requires root and is handled by the Dockerfile for deployment.
# For local development, run: sudo playwright install-deps
