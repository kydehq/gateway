#!/usr/bin/env bash
# Emit shields.io "endpoint" badge JSON for a coverage percentage.
#   usage: coverage-badge.sh <label> <percent>   (percent may be fractional)
# Consumed by the README badges via:
#   https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/<org>/<repo>/badges/<file>.json
set -euo pipefail

LABEL="$1"
PCT_INT=$(printf '%.0f' "$2")

if   [ "$PCT_INT" -ge 90 ]; then COLOR=brightgreen
elif [ "$PCT_INT" -ge 80 ]; then COLOR=green
elif [ "$PCT_INT" -ge 70 ]; then COLOR=yellowgreen
elif [ "$PCT_INT" -ge 60 ]; then COLOR=yellow
elif [ "$PCT_INT" -ge 50 ]; then COLOR=orange
else COLOR=red
fi

printf '{"schemaVersion":1,"label":"%s","message":"%s%%","color":"%s"}\n' \
  "$LABEL" "$PCT_INT" "$COLOR"
