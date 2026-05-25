#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="birdnet-mic-bridge"
INSTALL_DIR="/opt/mic_bridge"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# ── Helpers ───────────────────────────────────────────────

info()  { echo -e "\033[32m[+]\033[0m $*"; }
warn()  { echo -e "\033[33m[!]\033[0m $*"; }
error() { echo -e "\033[31m[x]\033[0m $*" >&2; }

usage() {
    cat <<EOF
Usage: sudo $0 [OPTIONS]

Install or uninstall the BirdNET mic bridge service.

Options:
  --user USER       Linux user to run the service as (default: auto-detect)
  --recs-dir PATH   StreamData directory (default: ~USER/BirdSongs/StreamData)
  --uninstall       Remove the bridge service and files
  -h, --help        Show this help
EOF
    exit 0
}

# ── Parse args ────────────────────────────────────────────

ACTION="install"
USER_OVERRIDE=""
RECS_DIR_OVERRIDE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --uninstall)  ACTION="uninstall"; shift ;;
        --user)       USER_OVERRIDE="$2"; shift 2 ;;
        --recs-dir)   RECS_DIR_OVERRIDE="$2"; shift 2 ;;
        -h|--help)    usage ;;
        *)            error "Unknown option: $1"; usage ;;
    esac
done

# ── Root check ────────────────────────────────────────────

if [[ $EUID -ne 0 ]]; then
    error "This script must be run as root (use sudo)"
    exit 1
fi

# ── Detect user ───────────────────────────────────────────

if [[ -n "$USER_OVERRIDE" ]]; then
    SVC_USER="$USER_OVERRIDE"
elif [[ -n "${SUDO_USER:-}" ]]; then
    SVC_USER="$SUDO_USER"
else
    SVC_USER="$(logname 2>/dev/null || echo pi)"
fi

SVC_HOME="$(eval echo ~"$SVC_USER")"

if [[ -n "$RECS_DIR_OVERRIDE" ]]; then
    RECS_DIR="$RECS_DIR_OVERRIDE"
elif [[ -d "$SVC_HOME/BirdSongs/StreamData" ]]; then
    RECS_DIR="$SVC_HOME/BirdSongs/StreamData"
else
    RECS_DIR="$SVC_HOME/BirdSongs/StreamData"
fi

# ── Uninstall ─────────────────────────────────────────────

if [[ "$ACTION" == "uninstall" ]]; then
    info "Uninstalling ${SERVICE_NAME}..."

    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        systemctl stop "$SERVICE_NAME"
        info "Stopped $SERVICE_NAME"
    fi

    if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
        systemctl disable "$SERVICE_NAME"
        info "Disabled $SERVICE_NAME"
    fi

    if [[ -f "$SERVICE_FILE" ]]; then
        rm -f "$SERVICE_FILE"
        systemctl daemon-reload
        info "Removed $SERVICE_FILE"
    fi

    if [[ -d "$INSTALL_DIR" ]]; then
        rm -rf "$INSTALL_DIR"
        info "Removed $INSTALL_DIR"
    fi

    if systemctl is-masked --quiet birdnet_recording.service 2>/dev/null; then
        systemctl unmask birdnet_recording.service
        info "Unmasked birdnet_recording.service"
    fi

    info "Uninstall complete."
    exit 0
fi

# ── Install ───────────────────────────────────────────────

info "Installing ${SERVICE_NAME}..."
info "  User:     $SVC_USER"
info "  Recs dir: $RECS_DIR"

# Copy bridge script
mkdir -p "$INSTALL_DIR"
cp "$SCRIPT_DIR/birdnet_mic_bridge.py" "$INSTALL_DIR/birdnet_mic_bridge.py"
chmod 644 "$INSTALL_DIR/birdnet_mic_bridge.py"
info "Copied bridge script to $INSTALL_DIR/"

# Create StreamData directory
mkdir -p "$RECS_DIR"
chown "$SVC_USER:$SVC_USER" "$RECS_DIR"
info "Ensured $RECS_DIR exists"

# Generate systemd service
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Pico 2W wireless mic bridge for BirdNET-Pi
After=network-online.target
Wants=network-online.target
Before=birdnet_analysis.service

[Service]
Type=simple
User=${SVC_USER}
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/birdnet_mic_bridge.py
Environment=RECS_DIR=${RECS_DIR}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
info "Created $SERVICE_FILE"

# Reload and enable
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
info "Service enabled and started"

# Mask BirdNET recording service if present
if systemctl list-unit-files | grep -q birdnet_recording.service; then
    if ! systemctl is-masked --quiet birdnet_recording.service 2>/dev/null; then
        warn "birdnet_recording.service detected — masking to prevent conflicts"
        systemctl stop birdnet_recording.service 2>/dev/null || true
        systemctl mask birdnet_recording.service
        info "Masked birdnet_recording.service"
    else
        info "birdnet_recording.service already masked"
    fi
fi

# Check ffmpeg
if ! command -v ffmpeg &>/dev/null; then
    warn "ffmpeg not found! Install it: sudo apt install ffmpeg"
else
    info "ffmpeg found: $(ffmpeg -version 2>&1 | head -1)"
fi

# Final status
echo ""
info "Installation complete!"
echo ""
systemctl status "$SERVICE_NAME" --no-pager || true
echo ""
info "Logs:   journalctl -u $SERVICE_NAME -f"
info "Stop:   sudo systemctl stop $SERVICE_NAME"
info "Remove: sudo $0 --uninstall"
