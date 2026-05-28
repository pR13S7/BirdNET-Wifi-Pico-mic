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
  --mode MODE       Client mode: pico, esp32, or both (default: pico)
  --uninstall       Remove the bridge service(s) and files
  -h, --help        Show this help

Modes:
  pico    — single bridge on port 5005 @ 16000 Hz (for Pico 2W)
  esp32   — single bridge on port 5006 @ 48000 Hz (for ESP32-S3)
  both    — two bridges: port 5005 (Pico) + port 5006 (ESP32)
EOF
    exit 0
}

# ── Parse args ────────────────────────────────────────────

ACTION="install"
USER_OVERRIDE=""
RECS_DIR_OVERRIDE=""
MODE="pico"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --uninstall)  ACTION="uninstall"; shift ;;
        --user)       USER_OVERRIDE="$2"; shift 2 ;;
        --recs-dir)   RECS_DIR_OVERRIDE="$2"; shift 2 ;;
        --mode)       MODE="$2"; shift 2 ;;
        -h|--help)    usage ;;
        *)            error "Unknown option: $1"; usage ;;
    esac
done

if [[ "$MODE" != "pico" && "$MODE" != "esp32" && "$MODE" != "both" ]]; then
    error "--mode must be pico, esp32, or both"
    exit 1
fi

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
    info "Uninstalling bridge services..."

    for svc in "$SERVICE_NAME" "${SERVICE_NAME}-2"; do
        svc_file="/etc/systemd/system/${svc}.service"
        if systemctl is-active --quiet "$svc" 2>/dev/null; then
            systemctl stop "$svc"
            info "Stopped $svc"
        fi
        if systemctl is-enabled --quiet "$svc" 2>/dev/null; then
            systemctl disable "$svc"
            info "Disabled $svc"
        fi
        if [[ -f "$svc_file" ]]; then
            rm -f "$svc_file"
            info "Removed $svc_file"
        fi
    done

    systemctl daemon-reload

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

info "Installing ${SERVICE_NAME} (mode: ${MODE})..."
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

# Generate systemd service(s)
install_service() {
    local svc_name="$1"
    local port="$2"
    local input_rate="$3"
    local svc_file="/etc/systemd/system/${svc_name}.service"

    cat > "$svc_file" <<EOF
[Unit]
Description=Wireless mic bridge for BirdNET-Pi (port ${port}, ${input_rate}Hz)
After=network-online.target
Wants=network-online.target
Before=birdnet_analysis.service

[Service]
Type=simple
User=${SVC_USER}
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/birdnet_mic_bridge.py
Environment=RECS_DIR=${RECS_DIR}
Environment=LISTEN_PORT=${port}
Environment=INPUT_RATE=${input_rate}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    info "Created $svc_file (port $port, ${input_rate}Hz)"
    systemctl enable "$svc_name"
    systemctl restart "$svc_name"
    info "Service $svc_name enabled and started"
}

if [[ "$MODE" == "pico" || "$MODE" == "both" ]]; then
    install_service "$SERVICE_NAME" 5005 16000
fi

if [[ "$MODE" == "esp32" || "$MODE" == "both" ]]; then
    install_service "${SERVICE_NAME}-2" 5006 48000
fi

systemctl daemon-reload

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
info "╔══════════════════════════════════════════════════════╗"
if [[ "$MODE" == "pico" || "$MODE" == "both" ]]; then
info "║  Pico 2W:   port 5005  @ 16000 Hz  (32 KB/s)    ║"
fi
if [[ "$MODE" == "esp32" || "$MODE" == "both" ]]; then
info "║  ESP32-S3:  port 5006  @ 48000 Hz  (96 KB/s)    ║"
fi
info "╚══════════════════════════════════════════════════════╝"
echo ""
info "Set SERVER_PORT in each board's main.py to match."
echo ""
if [[ "$MODE" == "pico" || "$MODE" == "both" ]]; then
    systemctl status "$SERVICE_NAME" --no-pager || true
    echo ""
fi
if [[ "$MODE" == "esp32" || "$MODE" == "both" ]]; then
    systemctl status "${SERVICE_NAME}-2" --no-pager || true
    echo ""
fi
info "Logs:"
if [[ "$MODE" == "pico" || "$MODE" == "both" ]]; then
info "  Pico:  journalctl -u $SERVICE_NAME -f"
fi
if [[ "$MODE" == "esp32" || "$MODE" == "both" ]]; then
info "  ESP32: journalctl -u ${SERVICE_NAME}-2 -f"
fi
info "Remove: sudo $0 --uninstall"
