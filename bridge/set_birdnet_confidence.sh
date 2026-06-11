#!/usr/bin/env bash
# Set BirdNET-Pi confidence level and restart analysis service.
# Usage: set_birdnet_confidence.sh <value>
#   e.g. set_birdnet_confidence.sh 0.7

set -euo pipefail

CONF="/etc/birdnet/birdnet.conf"
VALUE="${1:?Usage: $0 <confidence 0.0-1.0>}"

sed -i "s/^CONFIDENCE=.*/CONFIDENCE=${VALUE}/" "$CONF"
systemctl restart birdnet_analysis.service

logger -t birdnet-confidence "Set CONFIDENCE=${VALUE}, restarted birdnet_analysis"
