#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/common.sh"

main() {
    INSTALL_BLOCK="business-diagnostics"
    require_root
    assert_install_root
    assert_services_exist

    local out_dir
    out_dir="/opt/hiddify-manager/business-installer-diagnostics/$(date +%F-%H%M%S)"
    mkdir -p "$out_dir"

    {
        echo "release_version=$RELEASE_VERSION"
        echo "release_tag=$RELEASE_TAG"
        echo "git_commit=$RELEASE_COMMIT"
        echo "runtime_path=$(detect_runtime_path)"
    } > "$out_dir/release.txt"

    collect_checkpoint_status "$out_dir"
    systemctl cat "$SERVICE_PANEL" > "$out_dir/systemctl-cat-panel.txt" 2>&1 || true
    systemctl cat "$SERVICE_BG" > "$out_dir/systemctl-cat-bg.txt" 2>&1 || true
    systemctl show -p Environment "$SERVICE_PANEL" "$SERVICE_BG" > "$out_dir/systemctl-environment.txt" 2>&1 || true
    cp -f "$MANIFEST_PATH" "$out_dir/business-addon.manifest" 2>/dev/null || true
    cp -f "$BACKUP_ROOT/latest" "$out_dir/latest-backup.txt" 2>/dev/null || true
    mysql hiddifypanel -e "SELECT child_id, \`key\`, value FROM str_config WHERE \`key\`='db_version' OR \`key\` LIKE '%version%' ORDER BY child_id, \`key\`;" > "$out_dir/db-version.txt" 2>&1 || true

    echo "$out_dir"
}

main "$@"
