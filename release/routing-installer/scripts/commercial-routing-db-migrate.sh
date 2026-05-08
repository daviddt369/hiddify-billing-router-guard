#!/usr/bin/env bash
# DB migration for routing addon.
# Called from install-routing.sh after Python files are installed but BEFORE create_app/panel restart.
# Requires: BACKUP_DIR set in environment, DB_NAME set or defaulting to hiddifypanel.
set -Eeuo pipefail

DB_NAME="${DB_NAME:-hiddifypanel}"

die() { echo "[routing-db-migrate][ERROR] $*" >&2; exit 1; }
log() { echo "[routing-db-migrate] $*"; }

require_root() { [[ "$(id -u)" -eq 0 ]] || die "Run as root."; }
require_root

need_cmd() { command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"; }
need_cmd mysql
need_cmd mysqldump

[[ -n "${BACKUP_DIR:-}" && -d "$BACKUP_DIR" ]] \
    || die "BACKUP_DIR not set or missing. Must be called from install-routing.sh."

log "Starting routing DB migration for database: $DB_NAME"

# --- Step 1: DB dump before any changes ---
log "Backing up database to $BACKUP_DIR/db-dump.sql"
mysqldump "$DB_NAME" > "$BACKUP_DIR/db-dump.sql"

# --- Step 2: Schema snapshot for diagnostics ---
mysql "$DB_NAME" -e "SHOW TABLES; SHOW CREATE TABLE bool_config; SHOW CREATE TABLE str_config;" \
    > "$BACKUP_DIR/schema-snapshot.txt" 2>&1 || true

# --- Step 3: CREATE TABLE commercial_routing_custom_rule (idempotent) ---
# SQL taken directly from install-commercial-routing-addon.sh (battle-tested).
# normalized_value uses TEXT with prefix(255) for UNIQUE — MySQL/MariaDB-safe.
# Divergence from SQLAlchemy model noted:
#   - standalone INDEX on normalized_value omitted (TEXT without prefix unsupported in MySQL)
#   - UNIQUE uses (rule_type, normalized_value(255)) prefix, not full TEXT
#   - KEY(enabled) added for filter performance
log "Creating commercial_routing_custom_rule table if missing"
mysql "$DB_NAME" <<'SQL'
CREATE TABLE IF NOT EXISTS commercial_routing_custom_rule (
  id INT NOT NULL AUTO_INCREMENT,
  rule_type VARCHAR(32) NOT NULL,
  value TEXT NOT NULL,
  normalized_value TEXT NOT NULL,
  outbound_policy VARCHAR(32) NOT NULL DEFAULT 'direct_ru',
  enabled TINYINT(1) NOT NULL DEFAULT 1,
  comment TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY ix_commercial_routing_custom_rule_rule_type (rule_type),
  KEY ix_commercial_routing_custom_rule_enabled (enabled),
  UNIQUE KEY uq_commercial_routing_rule_unique (rule_type, normalized_value(255))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
SQL

# --- Step 4: Schema self-check ---
log "Verifying table schema"
schema_show="$(mysql "$DB_NAME" -e "SHOW CREATE TABLE commercial_routing_custom_rule\G" 2>&1)"

echo "$schema_show" | grep -q 'PRIMARY KEY' \
    || die "Schema check failed: PRIMARY KEY missing"
echo "$schema_show" | grep -q 'ix_commercial_routing_custom_rule_rule_type' \
    || die "Schema check failed: KEY ix_..._rule_type missing"
echo "$schema_show" | grep -q 'ix_commercial_routing_custom_rule_enabled' \
    || die "Schema check failed: KEY ix_..._enabled missing"
echo "$schema_show" | grep -q 'uq_commercial_routing_rule_unique' \
    || die "Schema check failed: UNIQUE KEY uq_commercial_routing_rule_unique missing"
echo "$schema_show" | grep -Eq '`?normalized_value`?\(255\)' \
    || die "Schema check failed: normalized_value(255) prefix missing from UNIQUE KEY"

log "Schema self-check passed"

# --- Step 5: Seed routing config defaults ---
# Logic (from install-commercial-routing-addon.sh):
#   bool_config:
#     commercial_routing_enable  - preserve existing (user may have enabled it already)
#     commercial_routing_installed - always force to 1
#     others - set default on first insert
#   str_config:
#     update only if current value is NULL or empty (preserve user settings)
log "Seeding routing config defaults"
mysql "$DB_NAME" <<'SQL'
INSERT INTO bool_config (child_id, `key`, value) VALUES
  (0, 'commercial_routing_enable',           0),
  (0, 'commercial_apply_to_xray',            1),
  (0, 'commercial_apply_to_singbox',         1),
  (0, 'commercial_ru_geoip_enabled',         1),
  (0, 'commercial_routing_installed',        1),
  (0, 'commercial_legacy_geosite_to_router', 1),
  (0, 'commercial_drop_bittorrent',          1)
ON DUPLICATE KEY UPDATE
  value = CASE
    WHEN `key` = 'commercial_routing_enable'           THEN value
    WHEN `key` = 'commercial_routing_installed'        THEN 1
    WHEN `key` = 'commercial_legacy_geosite_to_router' THEN value
    WHEN `key` = 'commercial_drop_bittorrent'          THEN value
    ELSE VALUES(value)
  END;

INSERT INTO str_config (child_id, `key`, value) VALUES
  (0, 'commercial_router_host',          '127.0.0.1'),
  (0, 'commercial_router_port',          '20808'),
  (0, 'commercial_router_protocol',      'socks5'),
  (0, 'commercial_domestic_policy',      'send_to_router'),
  (0, 'commercial_udp443_policy',        'keep_block'),
  (0, 'commercial_ru_domain_suffixes',   '.ru,.su,.xn--p1ai'),
  (0, 'commercial_default_global_policy','to_de'),
  (0, 'commercial_router_core_type',     'xray'),
  (0, 'commercial_de_tunnel_type',       'test_blackhole'),
  (0, 'commercial_de_endpoint',          ''),
  (0, 'commercial_de_public_key',        ''),
  (0, 'commercial_de_private_key_ref',   ''),
  (0, 'commercial_de_vless_uri',         ''),
  (0, 'commercial_de_trojan_uri',        '')
ON DUPLICATE KEY UPDATE
  value = CASE
    WHEN value IS NULL OR value = '' THEN VALUES(value)
    ELSE value
  END;
SQL

# --- Step 6: Verify commercial_routing_installed=1 ---
log "Verifying commercial_routing_installed flag"
installed_val="$(mysql "$DB_NAME" -N -B \
    -e "SELECT value FROM bool_config WHERE child_id=0 AND \`key\`='commercial_routing_installed';" 2>/dev/null \
    | head -n 1 || echo '')"
[[ "$installed_val" == "1" ]] \
    || die "commercial_routing_installed not set to 1 after migration. Got: '$installed_val'"

log "DB migration completed successfully"
log "commercial_routing_installed=1"

# --- Step 7: CREATE TABLE commercial_routing_upstream (idempotent) ---
log "Creating commercial_routing_upstream table if missing"
mysql "$DB_NAME" <<'SQL'
CREATE TABLE IF NOT EXISTS commercial_routing_upstream (
  id                 INT          NOT NULL AUTO_INCREMENT,
  name               VARCHAR(64)  NOT NULL,
  label              VARCHAR(128) NOT NULL DEFAULT '',
  enabled            TINYINT(1)   NOT NULL DEFAULT 1,
  priority           INT          NOT NULL DEFAULT 0,
  tunnel_type        VARCHAR(32)  NOT NULL DEFAULT 'test_blackhole',
  wg_endpoint        VARCHAR(255) NOT NULL DEFAULT '',
  wg_public_key      TEXT         NOT NULL DEFAULT '',
  wg_private_key_ref VARCHAR(512) NOT NULL DEFAULT '',
  wg_addresses       TEXT         NOT NULL DEFAULT '',
  wg_mtu             INT          NOT NULL DEFAULT 1280,
  vless_uri          TEXT         NOT NULL DEFAULT '',
  trojan_uri         TEXT         NOT NULL DEFAULT '',
  last_status        VARCHAR(32)  NOT NULL DEFAULT '',
  last_error         TEXT         NULL,
  last_checked_at    DATETIME     NULL,
  created_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY ix_upstream_enabled  (enabled),
  KEY ix_upstream_priority (priority),
  UNIQUE KEY uq_upstream_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
SQL

# --- Step 8: Schema self-check for commercial_routing_upstream ---
log "Verifying commercial_routing_upstream schema"
upstream_schema="$(mysql "$DB_NAME" -e "SHOW CREATE TABLE commercial_routing_upstream\G" 2>&1)"

echo "$upstream_schema" | grep -q 'PRIMARY KEY' \
    || die "Upstream schema check failed: PRIMARY KEY missing"
echo "$upstream_schema" | grep -q 'ix_upstream_enabled' \
    || die "Upstream schema check failed: KEY ix_upstream_enabled missing"
echo "$upstream_schema" | grep -q 'ix_upstream_priority' \
    || die "Upstream schema check failed: KEY ix_upstream_priority missing"
echo "$upstream_schema" | grep -q 'uq_upstream_name' \
    || die "Upstream schema check failed: UNIQUE KEY uq_upstream_name missing"
echo "$upstream_schema" | grep -q 'last_status' \
    || die "Upstream schema check failed: column last_status missing"
echo "$upstream_schema" | grep -q 'last_error' \
    || die "Upstream schema check failed: column last_error missing"
echo "$upstream_schema" | grep -q 'last_checked_at' \
    || die "Upstream schema check failed: column last_checked_at missing"

log "commercial_routing_upstream schema self-check passed"

# --- Step 9: Seed legacy upstream from commercial_de_* if table is empty ---
# Only seeds when:
#   1. commercial_routing_upstream is empty
#   2. commercial_de_tunnel_type is set and not 'test_blackhole'
# Does NOT touch or delete old commercial_de_* keys.
log "Checking legacy upstream seed condition"
upstream_count="$(mysql "$DB_NAME" -N -B \
    -e "SELECT COUNT(*) FROM commercial_routing_upstream;" 2>/dev/null | head -n 1 || echo '0')"
legacy_tunnel="$(mysql "$DB_NAME" -N -B \
    -e "SELECT value FROM str_config WHERE child_id=0 AND \`key\`='commercial_de_tunnel_type' LIMIT 1;" \
    2>/dev/null | head -n 1 || echo '')"

if [[ "$upstream_count" == "0" && -n "$legacy_tunnel" && "$legacy_tunnel" != "test_blackhole" ]]; then
    log "Seeding upstream-1 from legacy commercial_de_* config (tunnel_type=$legacy_tunnel)"
    mysql "$DB_NAME" <<'SQL'
INSERT IGNORE INTO commercial_routing_upstream
  (name, label, enabled, priority, tunnel_type,
   wg_endpoint, wg_public_key, wg_private_key_ref, wg_addresses, wg_mtu,
   vless_uri, trojan_uri)
SELECT
  'upstream-1',
  'Upstream 1',
  1,
  0,
  COALESCE(NULLIF((SELECT value FROM str_config WHERE child_id=0 AND `key`='commercial_de_tunnel_type' LIMIT 1), ''), 'test_blackhole'),
  COALESCE((SELECT value FROM str_config WHERE child_id=0 AND `key`='commercial_de_endpoint'         LIMIT 1), ''),
  COALESCE((SELECT value FROM str_config WHERE child_id=0 AND `key`='commercial_de_public_key'       LIMIT 1), ''),
  COALESCE((SELECT value FROM str_config WHERE child_id=0 AND `key`='commercial_de_private_key_ref'  LIMIT 1), ''),
  '',
  1280,
  COALESCE((SELECT value FROM str_config WHERE child_id=0 AND `key`='commercial_de_vless_uri'        LIMIT 1), ''),
  COALESCE((SELECT value FROM str_config WHERE child_id=0 AND `key`='commercial_de_trojan_uri'       LIMIT 1), '')
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM commercial_routing_upstream WHERE name='upstream-1');
SQL
    log "Legacy upstream-1 seeded"
else
    log "Skipping legacy seed: upstream_count=$upstream_count legacy_tunnel='$legacy_tunnel'"
fi

log "commercial_routing_upstream migration completed"

# --- Step 10: CREATE TABLE commercial_routing_rule_source (idempotent) ---
log "Creating commercial_routing_rule_source table if missing"
mysql "$DB_NAME" <<'SQL'
CREATE TABLE IF NOT EXISTS commercial_routing_rule_source (
  id              INT           NOT NULL AUTO_INCREMENT,
  name            VARCHAR(64)   NOT NULL,
  label           VARCHAR(128)  NOT NULL DEFAULT '',
  enabled         TINYINT(1)    NOT NULL DEFAULT 1,
  source_type     VARCHAR(32)   NOT NULL DEFAULT 'text',
  rule_family     VARCHAR(32)   NOT NULL DEFAULT 'domain',
  source_format   VARCHAR(32)   NOT NULL DEFAULT 'auto',
  outbound_policy VARCHAR(32)   NOT NULL DEFAULT 'direct_ru',
  content_text    MEDIUMTEXT    NOT NULL DEFAULT '',
  url             TEXT          NOT NULL DEFAULT '',
  local_path      VARCHAR(512)  NOT NULL DEFAULT '',
  last_status     VARCHAR(32)   NOT NULL DEFAULT '',
  last_error      TEXT          NULL,
  last_fetched_at DATETIME      NULL,
  last_hash       VARCHAR(64)   NOT NULL DEFAULT '',
  rules_count     INT           NOT NULL DEFAULT 0,
  created_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_rule_source_name (name),
  KEY ix_rule_source_enabled (enabled),
  KEY ix_rule_source_type (source_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
SQL

# --- Step 11: Schema self-check for commercial_routing_rule_source ---
log "Verifying commercial_routing_rule_source schema"
rule_source_schema="$(mysql "$DB_NAME" -e "SHOW CREATE TABLE commercial_routing_rule_source\G" 2>&1)"

echo "$rule_source_schema" | grep -q 'PRIMARY KEY' \
    || die "Rule source schema check failed: PRIMARY KEY missing"
echo "$rule_source_schema" | grep -q 'uq_rule_source_name' \
    || die "Rule source schema check failed: UNIQUE KEY uq_rule_source_name missing"
echo "$rule_source_schema" | grep -q 'ix_rule_source_enabled' \
    || die "Rule source schema check failed: KEY ix_rule_source_enabled missing"
echo "$rule_source_schema" | grep -q 'ix_rule_source_type' \
    || die "Rule source schema check failed: KEY ix_rule_source_type missing"
echo "$rule_source_schema" | grep -q 'last_status' \
    || die "Rule source schema check failed: column last_status missing"
echo "$rule_source_schema" | grep -q 'source_format' \
    || die "Rule source schema check failed: column source_format missing"
echo "$rule_source_schema" | grep -q 'rules_count' \
    || die "Rule source schema check failed: column rules_count missing"

log "commercial_routing_rule_source schema self-check passed"

# --- Step 12: Create allowed local-file directory ---
mkdir -p /opt/hiddify-manager/routing-lists
chmod 755 /opt/hiddify-manager/routing-lists
log "routing-lists directory ready: /opt/hiddify-manager/routing-lists"

log "DB migration Stage 2E completed"

# --- Step 13: Add source_id column to commercial_routing_custom_rule (idempotent) ---
log "Adding source_id column to commercial_routing_custom_rule if missing"
col_exists="$(mysql "$DB_NAME" -N -B \
    -e "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS \
        WHERE TABLE_SCHEMA='$DB_NAME' \
          AND TABLE_NAME='commercial_routing_custom_rule' \
          AND COLUMN_NAME='source_id';" 2>/dev/null | head -n1 || echo '0')"
if [[ "$col_exists" == "0" ]]; then
    mysql "$DB_NAME" <<'SQL'
ALTER TABLE commercial_routing_custom_rule
    ADD COLUMN source_id INT NULL DEFAULT NULL,
    ADD KEY ix_custom_rule_source_id (source_id),
    ADD CONSTRAINT fk_custom_rule_source
        FOREIGN KEY (source_id)
        REFERENCES commercial_routing_rule_source(id)
        ON DELETE SET NULL;
SQL
    log "source_id column added"
else
    log "source_id column already exists, skipping"
fi

# --- Step 14: Schema self-check ---
mysql "$DB_NAME" -e "SHOW CREATE TABLE commercial_routing_custom_rule\G" 2>/dev/null \
    | grep -q 'source_id' \
    || die "Schema check failed: source_id column missing from commercial_routing_custom_rule"
log "source_id schema check passed"

log "DB migration Stage 2F completed"

# --- Step 15: Advance db_version to 136 if the panel expects it ---
# Panel's init_db loop uses range(1, MAX_DB_VERSION) where MAX_DB_VERSION=136,
# meaning _v136 is never reachable via the migration loop (off-by-one in panel code).
# latest_db_version() returns 136, so celery's is_db_latest() check loops forever.
# The routing installer performs all _v136 work directly (tables + configs above),
# so it is safe to advance db_version here.
# Guard: only advance if current version is 134 or 135 (expected pre-routing state).
log "Checking db_version for celery beat compatibility"
current_dbver="$(mysql "$DB_NAME" -N -B \
    -e "SELECT value FROM str_config WHERE child_id=0 AND \`key\`='db_version';" \
    2>/dev/null | head -n1 || echo '0')"
if [[ "$current_dbver" -ge 134 && "$current_dbver" -lt 136 ]]; then
    mysql "$DB_NAME" <<'SQL'
UPDATE str_config SET value='136' WHERE `key`='db_version' AND child_id=0;
SQL
    log "db_version advanced from $current_dbver to 136 (celery beat fix)"
else
    log "db_version=$current_dbver — no advance needed"
fi
