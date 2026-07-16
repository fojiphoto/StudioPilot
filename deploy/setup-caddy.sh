#!/usr/bin/env bash
# Render the Caddyfile from deploy/Caddyfile.template with real values.
# Usage:  bash deploy/setup-caddy.sh 'your-portal-password'
# Reads DOMAIN and DASHBOARD_USER from .env.
set -euo pipefail
cd "$(dirname "$0")/.."

PASS="${1:?usage: setup-caddy.sh <portal-password>}"
DOMAIN=$(grep -E '^DOMAIN=' .env | cut -d= -f2-)
USER=$(grep -E '^DASHBOARD_USER=' .env | cut -d= -f2-)
: "${DOMAIN:?set DOMAIN in .env}"
: "${USER:?set DASHBOARD_USER in .env}"

HASH=$(docker run --rm caddy:2-alpine caddy hash-password --plaintext "$PASS")

python3 - "$DOMAIN" "$USER" "$HASH" <<'PY'
import sys
domain, user, h = sys.argv[1], sys.argv[2], sys.argv[3]
tpl = open("deploy/Caddyfile.template").read()
out = (tpl.replace("__DOMAIN__", domain)
          .replace("__USER__", user)
          .replace("__HASH__", h))
open("Caddyfile", "w").write(out)
print("wrote Caddyfile for", domain, "user", user)
PY
