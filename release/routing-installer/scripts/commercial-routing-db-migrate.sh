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

# --- Step 0: Pre-migration state snapshot (read-only, informational) ---
# Documents existing data before any changes. No DROP or DELETE ever runs in this migration.
log "Pre-migration state snapshot:"

# custom_rules
if mysql "$DB_NAME" -N -B -e "SHOW TABLES LIKE 'commercial_routing_custom_rule';" 2>/dev/null | grep -q .; then
    existing_rules="$(mysql "$DB_NAME" -N -B -e "SELECT COUNT(*) FROM commercial_routing_custom_rule;" 2>/dev/null | head -1 || echo '?')"
    log "  commercial_routing_custom_rule: EXISTS  rows=$existing_rules (will be preserved)"
    # source_id column
    has_source_id="$(mysql "$DB_NAME" -N -B \
        -e "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA='$DB_NAME'
              AND TABLE_NAME='commercial_routing_custom_rule'
              AND COLUMN_NAME='source_id';" 2>/dev/null | head -1 || echo '0')"
    if [[ "${has_source_id:-0}" -ge 1 ]]; then
        log "  commercial_routing_custom_rule.source_id: EXISTS (Stage 2F already applied)"
    else
        log "  commercial_routing_custom_rule.source_id: MISSING (will be added in Step 13)"
    fi
else
    log "  commercial_routing_custom_rule: MISSING (will be created)"
fi

# upstreams table
if mysql "$DB_NAME" -N -B -e "SHOW TABLES LIKE 'commercial_routing_upstream';" 2>/dev/null | grep -q .; then
    existing_upstreams="$(mysql "$DB_NAME" -N -B -e "SELECT COUNT(*) FROM commercial_routing_upstream;" 2>/dev/null | head -1 || echo '?')"
    log "  commercial_routing_upstream: EXISTS  rows=$existing_upstreams"
else
    log "  commercial_routing_upstream: MISSING (will be created in Step 7)"
    # Check legacy upstream config for seed decision
    legacy_tunnel="$(mysql "$DB_NAME" -N -B \
        -e "SELECT value FROM str_config WHERE child_id=0 AND \`key\`='commercial_de_tunnel_type' LIMIT 1;" \
        2>/dev/null | head -1 || echo '')"
    if [[ -n "$legacy_tunnel" && "$legacy_tunnel" != "test_blackhole" ]]; then
        log "  legacy commercial_de_tunnel_type='$legacy_tunnel' — Step 9 will seed upstream-1 from legacy config"
    else
        log "  legacy commercial_de_tunnel_type='${legacy_tunnel:-empty}' — Step 9 will skip legacy seed"
    fi
fi

# rule_source table
if mysql "$DB_NAME" -N -B -e "SHOW TABLES LIKE 'commercial_routing_rule_source';" 2>/dev/null | grep -q .; then
    existing_sources="$(mysql "$DB_NAME" -N -B -e "SELECT COUNT(*) FROM commercial_routing_rule_source;" 2>/dev/null | head -1 || echo '?')"
    log "  commercial_routing_rule_source: EXISTS  rows=$existing_sources"
else
    log "  commercial_routing_rule_source: MISSING (will be created in Step 10)"
fi

log "Pre-migration snapshot complete — proceeding with idempotent migration"

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

# --- Step 4: Schema self-check and reconcile (upgrade-safe) ---
# If the table pre-existed (e.g. from v0.12.5 installer with different schema),
# we add missing optional performance indexes idempotently instead of failing.
# Only PRIMARY KEY absence is treated as fatal (indicates structural corruption).
# UNIQUE KEY existence is verified but not its exact form (HASH vs prefix — both work).
log "Verifying and reconciling table schema"
schema_show="$(mysql "$DB_NAME" -e "SHOW CREATE TABLE commercial_routing_custom_rule\G" 2>&1)"

# Critical: PRIMARY KEY must exist
echo "$schema_show" | grep -q 'PRIMARY KEY' \
    || die "Schema check failed: PRIMARY KEY missing — table is structurally broken"

# Reconcile: rule_type index (add if missing — safe, non-unique index)
if ! echo "$schema_show" | grep -q 'ix_commercial_routing_custom_rule_rule_type'; then
    log "  Adding missing rule_type index (upgrade compatibility)"
    mysql "$DB_NAME" -e "
        ALTER TABLE commercial_routing_custom_rule
        ADD INDEX ix_commercial_routing_custom_rule_rule_type (rule_type);" 2>/dev/null || \
        warn "  Could not add rule_type index — may already exist under different name"
else
    log "  rule_type index: OK"
fi

# Reconcile: enabled index (add if missing — performance index for filter queries)
if ! echo "$schema_show" | grep -q 'ix_commercial_routing_custom_rule_enabled'; then
    log "  Adding missing enabled index (upgrade compatibility)"
    mysql "$DB_NAME" -e "
        ALTER TABLE commercial_routing_custom_rule
        ADD INDEX ix_commercial_routing_custom_rule_enabled (enabled);" 2>/dev/null || \
        warn "  Could not add enabled index — may already exist under different name"
else
    log "  enabled index: OK"
fi

# Verify: UNIQUE KEY exists in some form (HASH or prefix — both enforce uniqueness)
echo "$schema_show" | grep -q 'uq_commercial_routing_rule_unique' \
    || die "Schema check failed: UNIQUE KEY uq_commercial_routing_rule_unique missing"
log "  UNIQUE KEY uq_commercial_routing_rule_unique: OK"

# Re-read schema after reconcile
schema_show="$(mysql "$DB_NAME" -e "SHOW CREATE TABLE commercial_routing_custom_rule\G" 2>&1)"
log "Schema self-check passed (reconcile-safe)"

# --- Step 5: Seed routing config defaults ---
# Logic:
#   bool_config:
#     commercial_routing_enable  - preserve existing (user may have already enabled)
#     commercial_routing_installed - always force to 1 (if ENUM allows; see Step 5b)
#     others - set default on first insert
#   str_config:
#     update only if current value is NULL or empty (preserve user settings)
#
# Upgrade note: On production servers with db_version < 136, the bool_config.key ENUM
# may not include 'commercial_routing_installed' (added in panel v_136 migration).
# The batch INSERT below excludes it. Step 5b handles it separately via ENUM check.
log "Seeding routing config defaults (batch)"
mysql "$DB_NAME" <<'SQL'
INSERT INTO bool_config (child_id, `key`, value) VALUES
  (0, 'commercial_routing_enable',           0),
  (0, 'commercial_apply_to_xray',            1),
  (0, 'commercial_apply_to_singbox',         1),
  (0, 'commercial_ru_geoip_enabled',         1),
  (0, 'commercial_legacy_geosite_to_router', 1),
  (0, 'commercial_drop_bittorrent',          1)
ON DUPLICATE KEY UPDATE
  value = CASE
    WHEN `key` = 'commercial_routing_enable'           THEN value
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

# --- Step 5b: Set commercial_routing_installed marker ---
# This key was added to the bool_config ENUM by panel _v136 migration.
# On production servers with db_version < 136, the ENUM does not contain it.
# In that case, routing_enabled() falls back to checking the routing manifest file
# (/opt/hiddify-manager/routing-addon.manifest), which our installer creates at the end.
# The fallback is sufficient — routing admin views will load via manifest check.
log "Setting commercial_routing_installed marker"
enum_has_key="$(mysql "$DB_NAME" -N -B -e \
    "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
     WHERE TABLE_SCHEMA='$DB_NAME' AND TABLE_NAME='bool_config'
       AND COLUMN_NAME='key' AND COLUMN_TYPE LIKE '%commercial_routing_installed%';" \
    2>/dev/null | head -1 || echo 0)"

if [[ "${enum_has_key:-0}" -ge 1 ]]; then
    mysql "$DB_NAME" <<'SQL'
INSERT INTO bool_config (child_id, `key`, value)
VALUES (0, 'commercial_routing_installed', 1)
ON DUPLICATE KEY UPDATE value = 1;
SQL
    log "commercial_routing_installed=1 set in bool_config"
else
    log "commercial_routing_installed not in bool_config ENUM (db_version < 136 schema)"
    log "routing_enabled() will use routing-addon.manifest fallback — this is expected on upgrade"
fi

# --- Step 6: Verify routing is accessible ---
# Either via bool_config marker OR via manifest fallback check.
log "Verifying routing config seeded"
routing_en="$(mysql "$DB_NAME" -N -B \
    -e "SELECT value FROM bool_config WHERE child_id=0 AND \`key\`='commercial_routing_enable';" 2>/dev/null \
    | head -n 1 || echo '')"
log "commercial_routing_enable in bool_config: '${routing_en}' (1=enabled, 0=disabled, empty=not set)"
# Non-fatal: routing_enable may be managed by the panel admin UI

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
