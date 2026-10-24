#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

INSTALL_DIR="${INSTALL_DIR:-/opt/hiddify-manager}"
SECRETS_FILE="${SECRETS_FILE:-/etc/hiddify-panel/panel-secrets.env}"

TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
ENABLE_TELEGRAM_PAYMENTS="${ENABLE_TELEGRAM_PAYMENTS:-}"
TELEGRAM_PAYMENT_PROVIDER_TOKEN="${TELEGRAM_PAYMENT_PROVIDER_TOKEN:-}"
HIDDIFY_TELEGRAM_WEBHOOK_SECRET="${HIDDIFY_TELEGRAM_WEBHOOK_SECRET:-}"
HIDDIFY_TELEGRAM_WEBHOOK_DOMAIN="${HIDDIFY_TELEGRAM_WEBHOOK_DOMAIN:-}"
HIDDIFY_SUPPORT_URL="${HIDDIFY_SUPPORT_URL:-}"
HIDDIFY_TELEGRAM_REGISTRATION_MODE="${HIDDIFY_TELEGRAM_REGISTRATION_MODE:-}"

require_root() {
    if [[ "$(id -u)" -ne 0 ]]; then
        echo "This script must be run as root." >&2
        exit 1
    fi
}

generate_hex() {
    local bytes="${1:-16}"
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex "$bytes"
    else
        python3 - <<PY
import secrets
print(secrets.token_hex($bytes))
PY
    fi
}

prompt_value() {
    local var_name="$1"
    local prompt_text="$2"
    local default_value="${3:-}"
    local secret="${4:-0}"
    local current_value="${!var_name:-}"

    if [[ -n "$current_value" ]]; then
        return 0
    fi

    local answer=""
    if [[ "$secret" == "1" ]]; then
        if [[ -r /dev/tty ]]; then
            read -r -s -p "$prompt_text: " answer < /dev/tty
            echo > /dev/tty
        else
            read -r -s -p "$prompt_text: " answer
            echo
        fi
    elif [[ -n "$default_value" ]]; then
        if [[ -r /dev/tty ]]; then
            read -r -p "$prompt_text [$default_value]: " answer < /dev/tty
        else
            read -r -p "$prompt_text [$default_value]: " answer
        fi
        answer="${answer:-$default_value}"
    else
        if [[ -r /dev/tty ]]; then
            read -r -p "$prompt_text: " answer < /dev/tty
        else
            read -r -p "$prompt_text: " answer
        fi
    fi

    printf -v "$var_name" '%s' "$answer"
}

prompt_yes_no() {
    local var_name="$1"
    local prompt_text="$2"
    local default_value="${3:-N}"
    local current_value="${!var_name:-}"

    if [[ -n "$current_value" ]]; then
        return 0
    fi

    local answer=""
    if [[ -r /dev/tty ]]; then
        read -r -p "$prompt_text [$default_value]: " answer < /dev/tty
    else
        read -r -p "$prompt_text [$default_value]: " answer
    fi
    answer="${answer:-$default_value}"
    case "${answer,,}" in
        y|yes|1|true|on) printf -v "$var_name" '%s' "1" ;;
        *) printf -v "$var_name" '%s' "0" ;;
    esac
}

read_secret_file_value() {
    local key="$1"
    if [[ ! -f "$SECRETS_FILE" ]]; then
        return 0
    fi
    grep -E "^${key}=" "$SECRETS_FILE" | tail -n1 | cut -d= -f2- || true
}

normalize_webhook_domain() {
    local domain="$1"
    domain="${domain#http://}"
    domain="${domain#https://}"
    domain="${domain%%/*}"
    domain="${domain,,}"
    printf '%s' "$domain"
}

load_existing_values() {
    if [[ -z "$HIDDIFY_TELEGRAM_WEBHOOK_SECRET" ]]; then
        HIDDIFY_TELEGRAM_WEBHOOK_SECRET="$(read_secret_file_value "HIDDIFY_TELEGRAM_WEBHOOK_SECRET")"
    fi
    if [[ -z "$HIDDIFY_TELEGRAM_WEBHOOK_DOMAIN" ]]; then
        HIDDIFY_TELEGRAM_WEBHOOK_DOMAIN="$(read_secret_file_value "HIDDIFY_TELEGRAM_WEBHOOK_DOMAIN")"
    fi
    if [[ -z "$HIDDIFY_SUPPORT_URL" ]]; then
        HIDDIFY_SUPPORT_URL="$(read_secret_file_value "HIDDIFY_SUPPORT_URL")"
    fi
    if [[ -z "$HIDDIFY_TELEGRAM_REGISTRATION_MODE" ]]; then
        HIDDIFY_TELEGRAM_REGISTRATION_MODE="$(read_secret_file_value "HIDDIFY_TELEGRAM_REGISTRATION_MODE")"
    fi
}

is_first_setup_complete() {
    local value
    value="$(mysql -N hiddifypanel -e "select value from bool_config where \`key\`='first_setup' limit 1" 2>/dev/null | head -n 1 || true)"
    case "${value,,}" in
        0|false|"") return 0 ;;
        *) return 1 ;;
    esac
}

print_intro() {
    cat <<'EOF'
Commercial finalize

Run this only after:
1. base install completed
2. you opened the panel
3. Hiddify first setup saved the real domain

This step configures:
- Telegram bot token
- Telegram payment token
- webhook secret in server-only env
- Telegram webhook on the real domain
EOF
}

collect_inputs() {
    prompt_yes_no ENABLE_TELEGRAM_BOT "Enable Telegram bot?" "Y"
    if [[ "$ENABLE_TELEGRAM_BOT" == "1" ]]; then
        prompt_value TELEGRAM_BOT_TOKEN "Telegram bot token"
        prompt_value HIDDIFY_TELEGRAM_WEBHOOK_DOMAIN "Fixed Telegram webhook domain (FQDN, e.g. tgpanel.example.com)" "$HIDDIFY_TELEGRAM_WEBHOOK_DOMAIN"
        HIDDIFY_TELEGRAM_WEBHOOK_DOMAIN="$(normalize_webhook_domain "$HIDDIFY_TELEGRAM_WEBHOOK_DOMAIN")"
        prompt_value HIDDIFY_SUPPORT_URL "Support URL for user contact (Telegram/WhatsApp/site)" "$HIDDIFY_SUPPORT_URL"
        prompt_yes_no ENABLE_AUTO_REGISTRATION "Allow automatic user registration in bot?" "N"
        if [[ "$ENABLE_AUTO_REGISTRATION" == "1" ]]; then
            HIDDIFY_TELEGRAM_REGISTRATION_MODE="auto"
        else
            HIDDIFY_TELEGRAM_REGISTRATION_MODE="admin_only"
        fi
        if [[ -z "$HIDDIFY_TELEGRAM_WEBHOOK_SECRET" ]]; then
            HIDDIFY_TELEGRAM_WEBHOOK_SECRET="$(generate_hex 24)"
            echo "Generated Telegram webhook secret automatically."
        fi
        prompt_yes_no ENABLE_TELEGRAM_PAYMENTS "Enable Telegram payments (YooKassa) now?" "N"
        if [[ "$ENABLE_TELEGRAM_PAYMENTS" == "1" ]]; then
            prompt_value TELEGRAM_PAYMENT_PROVIDER_TOKEN "YooKassa provider token for Telegram (from BotFather)" "" 1
        else
            TELEGRAM_PAYMENT_PROVIDER_TOKEN=""
        fi
    else
        TELEGRAM_BOT_TOKEN=""
        HIDDIFY_TELEGRAM_WEBHOOK_SECRET=""
        HIDDIFY_TELEGRAM_WEBHOOK_DOMAIN=""
        HIDDIFY_SUPPORT_URL=""
        HIDDIFY_TELEGRAM_REGISTRATION_MODE="admin_only"
        ENABLE_TELEGRAM_PAYMENTS="0"
        TELEGRAM_PAYMENT_PROVIDER_TOKEN=""
    fi
}

print_summary() {
    echo
    echo "Finalize summary"
    echo "- Telegram bot: $( [[ -n "$TELEGRAM_BOT_TOKEN" ]] && echo enabled || echo disabled )"
    if [[ -n "$TELEGRAM_BOT_TOKEN" ]]; then
        echo "- Fixed Telegram webhook domain: ${HIDDIFY_TELEGRAM_WEBHOOK_DOMAIN:-auto (panel domain selection)}"
        echo "- Support URL: ${HIDDIFY_SUPPORT_URL:-not set}"
        echo "- Bot registration mode: ${HIDDIFY_TELEGRAM_REGISTRATION_MODE:-admin_only}"
    fi
    echo "- Telegram payments: $( [[ "$ENABLE_TELEGRAM_PAYMENTS" == "1" ]] && echo enabled || echo disabled )"
    echo
}

run_finalize() {
    TELEGRAM_BOT_TOKEN="$TELEGRAM_BOT_TOKEN" \
    ENABLE_TELEGRAM_PAYMENTS="$ENABLE_TELEGRAM_PAYMENTS" \
    TELEGRAM_PAYMENT_PROVIDER_TOKEN="$TELEGRAM_PAYMENT_PROVIDER_TOKEN" \
    HIDDIFY_TELEGRAM_WEBHOOK_SECRET="$HIDDIFY_TELEGRAM_WEBHOOK_SECRET" \
    HIDDIFY_TELEGRAM_WEBHOOK_DOMAIN="$HIDDIFY_TELEGRAM_WEBHOOK_DOMAIN" \
    HIDDIFY_SUPPORT_URL="$HIDDIFY_SUPPORT_URL" \
    HIDDIFY_TELEGRAM_REGISTRATION_MODE="$HIDDIFY_TELEGRAM_REGISTRATION_MODE" \
    INSTALL_DIR="$INSTALL_DIR" \
    bash "$SCRIPT_DIR/post-install-commercial.sh"
}

main() {
    require_root
    load_existing_values
    if ! is_first_setup_complete; then
        echo "Complete Hiddify first setup first, then run this script again." >&2
        exit 1
    fi
    print_intro
    collect_inputs
    print_summary
    run_finalize
}

main "$@"
