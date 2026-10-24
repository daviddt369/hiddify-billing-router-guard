#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/hiddify-manager}"
ENV_DIR="${ENV_DIR:-/etc/hiddify-panel}"
ENV_FILE="${ENV_FILE:-$ENV_DIR/anti-share.env}"
SERVICE_NAME="hiddify-anti-share.service"
TIMER_NAME="hiddify-anti-share.timer"

require_root() {
    [[ "$(id -u)" -eq 0 ]] || {
        echo "[anti-share][ERROR] run as root" >&2
        exit 1
    }
}

ensure_env_file() {
    mkdir -p "$ENV_DIR"
    chmod 700 "$ENV_DIR"
    if [[ -f "$ENV_FILE" ]]; then
        return 0
    fi
    cat >"$ENV_FILE" <<'EOF'
HIDDIFY_ANTI_SHARE_ENABLED=1
HIDDIFY_ANTI_SHARE_WINDOW_SECONDS=120
HIDDIFY_ANTI_SHARE_LEARNING_DAYS=7
HIDDIFY_ANTI_SHARE_RETENTION_DAYS=45
HIDDIFY_ANTI_SHARE_TRUSTED_RECENT_DAYS=7
HIDDIFY_ANTI_SHARE_TRUST_DECAY_PER_DAY=0.15
HIDDIFY_ANTI_SHARE_SCORE_DECAY_CLEAN=0.25
HIDDIFY_ANTI_SHARE_SCORE_PLUS1=0.25
HIDDIFY_ANTI_SHARE_SCORE_PLUS2=0.50
HIDDIFY_ANTI_SHARE_SCORE_PLUS3=1.00
HIDDIFY_ANTI_SHARE_SUSPECT_SCORE=0.50
HIDDIFY_ANTI_SHARE_WARN_SCORE=0.75
HIDDIFY_ANTI_SHARE_BLOCK_SCORE=1.00
HIDDIFY_ANTI_SHARE_SEVERE_NEW_IP_THRESHOLD=3
HIDDIFY_ANTI_SHARE_SEVERE_TRAFFIC_RATIO=5.0
HIDDIFY_ANTI_SHARE_BAN_SECONDS=3600
HIDDIFY_ANTI_SHARE_TELEGRAM_ENABLED=1
HIDDIFY_ANTI_SHARE_NFT_ENABLED=1
HIDDIFY_ANTI_SHARE_NFT_DRY_RUN=0
HIDDIFY_ANTI_SHARE_NFT_HELPER=/opt/hiddify-manager/common/hiddify-antishare-nft.sh
HIDDIFY_ANTI_SHARE_SCAN_LIMIT=1000
HIDDIFY_ANTI_SHARE_SNAPSHOT_LIMIT=32
EOF
    chmod 600 "$ENV_FILE"
}

ensure_paths() {
    install -d -o root -g root -m 755 "$INSTALL_DIR/common"
    install -d -o root -g root -m 755 /etc/systemd/system
    install -d -o root -g root -m 755 /etc/systemd/system/hiddify-anti-share.service.d
    install -d -o root -g root -m 755 /etc/systemd/system/hiddify-xray.service.d
}

prepare_access_paths() {
    local system_log_dir="$INSTALL_DIR/log/system"
    local access_log_path="$system_log_dir/xray.access.log"
    local access_state_path="$system_log_dir/xray.access.state.json"

    install -d -o root -g root -m 755 "$system_log_dir"
    if [[ ! -e "$access_log_path" ]]; then
        install -o root -g hiddify-panel -m 0640 /dev/null "$access_log_path"
    else
        chown root:hiddify-panel "$access_log_path"
        chmod 0640 "$access_log_path"
    fi

    if [[ ! -e "$access_state_path" ]]; then
        install -o hiddify-panel -g hiddify-panel -m 0664 /dev/null "$access_state_path"
    else
        chown hiddify-panel:hiddify-panel "$access_state_path"
        chmod 0664 "$access_state_path"
    fi
}

install_xray_dropin() {
    local dropin_src="$INSTALL_DIR/systemd/anti-share-access.conf"
    local dropin_dst="/etc/systemd/system/hiddify-xray.service.d/anti-share-access.conf"
    if [[ -f "$dropin_src" ]]; then
        install -D -m 0644 "$dropin_src" "$dropin_dst"
    fi
}

check_python_entrypoint() {
    su hiddify-panel -s /bin/bash -c "set -a; [ -f '$ENV_FILE' ] && . '$ENV_FILE'; set +a; cd '$INSTALL_DIR/hiddify-panel' && /opt/hiddify-manager/.venv313/bin/python -m hiddifypanel.antishare.runner --check"
}

enable_units() {
    systemctl daemon-reload
    systemctl restart hiddify-xray
    systemctl enable --now "$TIMER_NAME"
    systemctl start "$SERVICE_NAME"
}

print_summary() {
    echo "Anti-share addon installed."
    echo "Env file: $ENV_FILE"
    echo "Timer: $TIMER_NAME"
    echo "Service: $SERVICE_NAME"
    echo "NFT helper: /opt/hiddify-manager/common/hiddify-antishare-nft.sh"
}

main() {
    require_root
    ensure_env_file
    ensure_paths
    prepare_access_paths
    install_xray_dropin
    check_python_entrypoint
    enable_units
    print_summary
}

main "$@"
