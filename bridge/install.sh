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
  --user USER         Linux user to run the service as (default: auto-detect)
  --recs-dir PATH     StreamData directory (default: ~USER/BirdSongs/StreamData)
  --mode MODE         Client mode: pico, esp32, or both (default: pico)
  --telegram-script PATH
                      Optional telegram sender script (default: auto-detect
                      /usr/local/bin/telegram-send.sh if executable)
  --notch-pico HZ     Notch-filter the Pico stream at HZ to remove the INMP441
                      idle tone (recommended: 3575; 0 = off, default)
  --notch-esp32 HZ    Notch-filter the ESP32 stream at HZ (recommended: 3575;
                      0 = off, default)
  --gentle            Minimal bridge processing for all sources: ffmpeg
                      click removal off + soft 1-pole high-pass (this is the
                      default; flag just makes it explicit)
  --declick MODE      Restore aggressive bridge-side click removal (ffmpeg
                      adeclick + steeper 2-pole high-pass) for pico, esp32,
                      or both. On-device DSP still runs regardless.
  --uninstall         Remove the bridge service(s) and files
  -h, --help          Show this help

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
NOTCH_PICO="0"
NOTCH_ESP32="0"
TELEGRAM_SCRIPT_OVERRIDE=""
# Gentle defaults (declick off, soft 1-pole high-pass). --declick re-enables
# the aggressive ffmpeg click removal + steeper 2-pole high-pass per source.
DECLICK_PICO="0"
DECLICK_ESP32="0"
HP_POLES_PICO="1"
HP_POLES_ESP32="1"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --uninstall)    ACTION="uninstall"; shift ;;
        --user)         USER_OVERRIDE="$2"; shift 2 ;;
        --recs-dir)     RECS_DIR_OVERRIDE="$2"; shift 2 ;;
        --mode)         MODE="$2"; shift 2 ;;
        --telegram-script) TELEGRAM_SCRIPT_OVERRIDE="$2"; shift 2 ;;
        --notch-pico)   NOTCH_PICO="$2"; shift 2 ;;
        --notch-esp32)  NOTCH_ESP32="$2"; shift 2 ;;
        --gentle)
            DECLICK_PICO="0"; HP_POLES_PICO="1"
            DECLICK_ESP32="0"; HP_POLES_ESP32="1"
            shift ;;
        --declick)
            case "$2" in
                pico)  DECLICK_PICO="1"; HP_POLES_PICO="2" ;;
                esp32) DECLICK_ESP32="1"; HP_POLES_ESP32="2" ;;
                both)  DECLICK_PICO="1"; HP_POLES_PICO="2"
                       DECLICK_ESP32="1"; HP_POLES_ESP32="2" ;;
                *)     error "--declick must be pico, esp32, or both"; exit 1 ;;
            esac
            shift 2 ;;
        -h|--help)      usage ;;
        *)              error "Unknown option: $1"; usage ;;
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

    CRON_TAG="# birdnet-confidence-scheduler"
    crontab -l 2>/dev/null | grep -v "$CRON_TAG" | crontab - 2>/dev/null || true
    info "Removed confidence scheduler cron jobs"

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

TELEGRAM_SEND_CMD=""
if [[ -n "$TELEGRAM_SCRIPT_OVERRIDE" ]]; then
    TELEGRAM_SEND_CMD="$TELEGRAM_SCRIPT_OVERRIDE"
elif [[ -x "/usr/local/bin/telegram-send.sh" ]]; then
    TELEGRAM_SEND_CMD="/usr/local/bin/telegram-send.sh"
fi

if [[ -n "$TELEGRAM_SEND_CMD" && ! -f "$TELEGRAM_SEND_CMD" ]]; then
    error "Telegram script not found: $TELEGRAM_SEND_CMD"
    exit 1
fi

if [[ -n "$TELEGRAM_SEND_CMD" ]]; then
    info "  Telegram: $TELEGRAM_SEND_CMD"
    if command -v runuser >/dev/null 2>&1; then
        if runuser -u "$SVC_USER" -- test -r "$TELEGRAM_SEND_CMD"; then
            info "  Telegram access for $SVC_USER: readable"
        else
            warn "Telegram script is not readable by $SVC_USER; notifications will fail."
            warn "If this script is shared with Transmission, keep current owner/group and grant ACL:"
            warn "  sudo apt install -y acl"
            warn "  sudo setfacl -m u:${SVC_USER}:rx ${TELEGRAM_SEND_CMD}"
        fi
    else
        warn "runuser not found; unable to verify telegram script readability for $SVC_USER"
    fi
else
    info "  Telegram: disabled (script not found)"
fi

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
    local source_tag="$4"
    local notch_hz="${5:-0}"
    local declick="${6:-0}"
    local hp_poles="${7:-1}"
    local svc_file="/etc/systemd/system/${svc_name}.service"
    local source_label="$source_tag"
    local telegram_env=""
    local declick_label="off"
    [[ "$declick" == "1" ]] && declick_label="on"

    if [[ "$source_tag" == "pico" ]]; then
        source_label="Pico-2W"
    elif [[ "$source_tag" == "esp32" ]]; then
        source_label="ESP32"
    fi

    if [[ -n "$TELEGRAM_SEND_CMD" ]]; then
        telegram_env="Environment=TELEGRAM_SEND_CMD=${TELEGRAM_SEND_CMD}"
    fi

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
Environment=SOURCE_TAG=${source_tag}
Environment=SOURCE_LABEL=${source_label}
Environment=SERVICE_NAME=${svc_name}
Environment=NOTCH_HZ=${notch_hz}
Environment=DECLICK_ENABLE=${declick}
Environment=HIGHPASS_POLES=${hp_poles}
${telegram_env}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    info "Created $svc_file (port $port, ${input_rate}Hz, tag=${source_tag}, notch=${notch_hz}Hz, declick=${declick_label}, hp_poles=${hp_poles})"
    systemctl enable "$svc_name"
    systemctl restart "$svc_name"
    info "Service $svc_name enabled and started"
}

if [[ "$MODE" == "pico" || "$MODE" == "both" ]]; then
    install_service "$SERVICE_NAME" 5005 16000 "pico" "$NOTCH_PICO" "$DECLICK_PICO" "$HP_POLES_PICO"
fi

if [[ "$MODE" == "esp32" || "$MODE" == "both" ]]; then
    install_service "${SERVICE_NAME}-2" 5006 48000 "esp32" "$NOTCH_ESP32" "$DECLICK_ESP32" "$HP_POLES_ESP32"
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

# Install confidence scheduler cron jobs
CONFIDENCE_SCRIPT="${INSTALL_DIR}/set_birdnet_confidence.sh"
cp "$SCRIPT_DIR/set_birdnet_confidence.sh" "$CONFIDENCE_SCRIPT"
chmod 755 "$CONFIDENCE_SCRIPT"
info "Installed confidence scheduler to $CONFIDENCE_SCRIPT"

CRON_TAG="# birdnet-confidence-scheduler"
# Remove old entries if any
crontab -l 2>/dev/null | grep -v "$CRON_TAG" | crontab - 2>/dev/null || true
# Add new entries
(crontab -l 2>/dev/null; echo "0 4 * * * $CONFIDENCE_SCRIPT 0.4 $CRON_TAG"; echo "0 21 * * * $CONFIDENCE_SCRIPT 0.7 $CRON_TAG") | crontab -
info "Cron: CONFIDENCE=0.4 at 04:00, CONFIDENCE=0.7 at 21:00"

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
if [[ -n "$TELEGRAM_SEND_CMD" ]]; then
info "Telegram notifications enabled for connect/disconnect events."
else
warn "Telegram notifications are disabled. Install a sender script and rerun with --telegram-script /path/to/telegram-send.sh"
fi
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
