#!/usr/bin/env bash
# DB migration for anti-share addon.
# Called from install-antishare.sh after Python files are installed but BEFORE create_app/panel restart.
# Requires: BACKUP_DIR set in environment, DB_NAME set or defaulting to hiddifypanel.
set -Eeuo pipefail

DB_NAME="${DB_NAME:-hiddifypanel}"

die() { echo "[antishare-db-migrate][ERROR] $*" >&2; exit 1; }
log() { echo "[antishare-db-migrate] $*"; }

require_root() { [[ "$(id -u)" -eq 0 ]] || die "Run as root."; }
require_root

need_cmd() { command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"; }
need_cmd mysql
need_cmd mysqldump

[[ -n "${BACKUP_DIR:-}" && -d "$BACKUP_DIR" ]] \
    || die "BACKUP_DIR not set or missing. Must be called from install-antishare.sh."

log "Starting anti-share DB migration for database: $DB_NAME"

# --- Step 1: DB dump before any changes ---
log "Backing up database to $BACKUP_DIR/db-dump.sql"
mysqldump "$DB_NAME" > "$BACKUP_DIR/db-dump.sql"

# --- Step 2: Schema snapshot for diagnostics ---
mysql "$DB_NAME" -e "SHOW TABLES;" \
    > "$BACKUP_DIR/schema-snapshot.txt" 2>&1 || true

# --- Step 3: CREATE TABLE anti_share_config (idempotent) ---
log "Creating anti_share_config table if missing"
mysql "$DB_NAME" <<'SQL'
CREATE TABLE IF NOT EXISTS anti_share_config (
  id                         INT          NOT NULL AUTO_INCREMENT,
  enabled                    TINYINT(1)   NOT NULL DEFAULT 1,
  window_seconds             INT          NOT NULL DEFAULT 120,
  learning_days              INT          NOT NULL DEFAULT 7,
  retention_days             INT          NOT NULL DEFAULT 45,
  trusted_recent_days        INT          NOT NULL DEFAULT 7,
  trust_decay_per_day        DOUBLE       NOT NULL DEFAULT 0.15,
  score_decay_clean          DOUBLE       NOT NULL DEFAULT 0.25,
  score_plus1                DOUBLE       NOT NULL DEFAULT 0.25,
  score_plus2                DOUBLE       NOT NULL DEFAULT 0.50,
  score_plus3                DOUBLE       NOT NULL DEFAULT 1.00,
  suspect_score              DOUBLE       NOT NULL DEFAULT 0.50,
  warn_score                 DOUBLE       NOT NULL DEFAULT 0.75,
  block_score                DOUBLE       NOT NULL DEFAULT 1.00,
  severe_new_ip_threshold    INT          NOT NULL DEFAULT 3,
  severe_traffic_ratio       DOUBLE       NOT NULL DEFAULT 5.0,
  ban_seconds                INT          NOT NULL DEFAULT 3600,
  telegram_enabled           TINYINT(1)   NOT NULL DEFAULT 0,
  nft_enabled                TINYINT(1)   NOT NULL DEFAULT 0,
  nft_dry_run                TINYINT(1)   NOT NULL DEFAULT 1,
  nft_helper                 VARCHAR(512) NOT NULL DEFAULT '/opt/hiddify-manager/common/hiddify-antishare-nft.sh',
  scan_limit                 INT          NOT NULL DEFAULT 1000,
  current_ip_snapshot_limit  INT          NOT NULL DEFAULT 32,
  service_name               VARCHAR(128) NOT NULL DEFAULT 'hiddify-anti-share',
  created_at                 DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at                 DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
SQL

# --- Step 4: CREATE TABLE anti_share_state (idempotent) ---
log "Creating anti_share_state table if missing"
mysql "$DB_NAME" <<'SQL'
CREATE TABLE IF NOT EXISTS anti_share_state (
  id                  INT          NOT NULL AUTO_INCREMENT,
  user_id             INT          NOT NULL,
  allowed_ip_count    INT          NOT NULL DEFAULT 1,
  current_ip_count    INT          NOT NULL DEFAULT 0,
  score               DOUBLE       NOT NULL DEFAULT 0.0,
  state               VARCHAR(16)  NOT NULL DEFAULT 'learning',
  learning_until      DATETIME     NULL,
  violation_started_at DATETIME    NULL,
  warned_at           DATETIME     NULL,
  blocked_at          DATETIME     NULL,
  ban_until           DATETIME     NULL,
  traffic_multiplier  DOUBLE       NOT NULL DEFAULT 1.0,
  last_cycle_usage    BIGINT       NOT NULL DEFAULT 0,
  last_ips_snapshot   TEXT         NOT NULL DEFAULT '[]',
  created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_anti_share_state_user_id (user_id),
  CONSTRAINT fk_anti_share_state_user
    FOREIGN KEY (user_id) REFERENCES user(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
SQL

# --- Step 5: CREATE TABLE anti_share_ip_profile (idempotent) ---
log "Creating anti_share_ip_profile table if missing"
mysql "$DB_NAME" <<'SQL'
CREATE TABLE IF NOT EXISTS anti_share_ip_profile (
  id              INT         NOT NULL AUTO_INCREMENT,
  user_id         INT         NOT NULL,
  ip              VARCHAR(64) NOT NULL,
  first_seen_at   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  seen_cycles     INT         NOT NULL DEFAULT 0,
  seen_days       INT         NOT NULL DEFAULT 0,
  total_hits      INT         NOT NULL DEFAULT 0,
  trust_score     DOUBLE      NOT NULL DEFAULT 0.0,
  is_trusted      TINYINT(1)  NOT NULL DEFAULT 0,
  last_banned_at  DATETIME    NULL,
  last_ban_until  DATETIME    NULL,
  created_at      DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY ix_anti_share_ip_profile_user_id (user_id),
  KEY ix_anti_share_ip_profile_ip (ip),
  UNIQUE KEY uq_anti_share_user_ip (user_id, ip),
  CONSTRAINT fk_anti_share_ip_profile_user
    FOREIGN KEY (user_id) REFERENCES user(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
SQL

# --- Step 6: CREATE TABLE anti_share_event (idempotent) ---
log "Creating anti_share_event table if missing"
mysql "$DB_NAME" <<'SQL'
CREATE TABLE IF NOT EXISTS anti_share_event (
  id           INT         NOT NULL AUTO_INCREMENT,
  user_id      INT         NOT NULL,
  event_type   VARCHAR(64) NOT NULL,
  ip           VARCHAR(64) NULL,
  score_before DOUBLE      NOT NULL DEFAULT 0.0,
  score_after  DOUBLE      NOT NULL DEFAULT 0.0,
  state_before VARCHAR(16) NOT NULL DEFAULT '',
  state_after  VARCHAR(16) NOT NULL DEFAULT '',
  payload      TEXT        NOT NULL DEFAULT '{}',
  created_at   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY ix_anti_share_event_user_id (user_id),
  KEY ix_anti_share_event_type (event_type),
  KEY ix_anti_share_event_ip (ip),
  CONSTRAINT fk_anti_share_event_user
    FOREIGN KEY (user_id) REFERENCES user(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
SQL

# --- Step 7: CREATE TABLE anti_share_user_override (idempotent) ---
log "Creating anti_share_user_override table if missing"
mysql "$DB_NAME" <<'SQL'
CREATE TABLE IF NOT EXISTS anti_share_user_override (
  id         INT          NOT NULL AUTO_INCREMENT,
  user_id    INT          NOT NULL,
  disabled   TINYINT(1)   NOT NULL DEFAULT 0,
  note       VARCHAR(512) NOT NULL DEFAULT '',
  created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_anti_share_user_override_user_id (user_id),
  CONSTRAINT fk_anti_share_user_override_user
    FOREIGN KEY (user_id) REFERENCES user(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
SQL

# --- Step 8: Schema self-checks for all 5 tables ---
log "Verifying anti_share_config schema"
cfg_schema="$(mysql "$DB_NAME" -e "SHOW CREATE TABLE anti_share_config\G" 2>&1)"
echo "$cfg_schema" | grep -q 'PRIMARY KEY'          || die "anti_share_config: PRIMARY KEY missing"
echo "$cfg_schema" | grep -q 'nft_enabled'          || die "anti_share_config: nft_enabled missing"
echo "$cfg_schema" | grep -q 'telegram_enabled'     || die "anti_share_config: telegram_enabled missing"
echo "$cfg_schema" | grep -q 'nft_dry_run'          || die "anti_share_config: nft_dry_run missing"
log "anti_share_config schema OK"

log "Verifying anti_share_state schema"
state_schema="$(mysql "$DB_NAME" -e "SHOW CREATE TABLE anti_share_state\G" 2>&1)"
echo "$state_schema" | grep -q 'PRIMARY KEY'                     || die "anti_share_state: PRIMARY KEY missing"
echo "$state_schema" | grep -qE 'UNIQUE KEY.*user_id' \
    || die "anti_share_state: UNIQUE user_id missing"
echo "$state_schema" | grep -q 'fk_anti_share_state_user\|FOREIGN KEY' || die "anti_share_state: FK user missing"
log "anti_share_state schema OK"

log "Verifying anti_share_ip_profile schema"
prof_schema="$(mysql "$DB_NAME" -e "SHOW CREATE TABLE anti_share_ip_profile\G" 2>&1)"
echo "$prof_schema" | grep -q 'PRIMARY KEY'              || die "anti_share_ip_profile: PRIMARY KEY missing"
echo "$prof_schema" | grep -q 'uq_anti_share_user_ip'   || die "anti_share_ip_profile: UNIQUE user_id+ip missing"
echo "$prof_schema" | grep -q 'trust_score'              || die "anti_share_ip_profile: trust_score missing"
log "anti_share_ip_profile schema OK"

log "Verifying anti_share_event schema"
event_schema="$(mysql "$DB_NAME" -e "SHOW CREATE TABLE anti_share_event\G" 2>&1)"
echo "$event_schema" | grep -q 'PRIMARY KEY'              || die "anti_share_event: PRIMARY KEY missing"
echo "$event_schema" | grep -qE 'KEY.*event_type' || die "anti_share_event: KEY event_type missing"
log "anti_share_event schema OK"

log "Verifying anti_share_user_override schema"
ovr_schema="$(mysql "$DB_NAME" -e "SHOW CREATE TABLE anti_share_user_override\G" 2>&1)"
echo "$ovr_schema" | grep -q 'PRIMARY KEY'                              || die "anti_share_user_override: PRIMARY KEY missing"
echo "$ovr_schema" | grep -qE 'UNIQUE KEY.*user_id' \
    || die "anti_share_user_override: UNIQUE user_id missing"
log "anti_share_user_override schema OK"

log "All 5 anti-share table schemas verified"

# --- Step 9: Seed anti_share_config with safe defaults (only if empty) ---
# Safe defaults applied ONLY on first install (empty table).
# On upgrade, existing admin settings are intentional and MUST NOT be overwritten:
#   nft_enabled, nft_dry_run, telegram_enabled may be set to production values.
# The WHERE NOT EXISTS guard ensures this is always idempotent.
existing_cfg_count="$(mysql "$DB_NAME" -N -B -e "SELECT COUNT(*) FROM anti_share_config;" 2>/dev/null | head -n1 || echo '0')"

if [[ "$existing_cfg_count" -ge 1 ]]; then
    # Existing config found — log current admin settings and skip seeding entirely.
    # Do NOT run the INSERT: production tables may lack default values for columns
    # added after the initial install (e.g. created_at NOT NULL), causing MySQL
    # ERROR 1364 even when WHERE NOT EXISTS prevents actual row insertion.
    existing_nft="$(mysql "$DB_NAME" -N -B -e "SELECT nft_enabled FROM anti_share_config LIMIT 1;" 2>/dev/null | head -n1 || echo '?')"
    existing_dry="$(mysql "$DB_NAME" -N -B -e "SELECT nft_dry_run FROM anti_share_config LIMIT 1;" 2>/dev/null | head -n1 || echo '?')"
    existing_tg="$(mysql "$DB_NAME" -N -B -e "SELECT telegram_enabled FROM anti_share_config LIMIT 1;" 2>/dev/null | head -n1 || echo '?')"
    log "existing anti_share_config found, preserving admin settings:"
    log "  nft_enabled=$existing_nft  nft_dry_run=$existing_dry  telegram_enabled=$existing_tg"
    log "  (safe defaults NOT applied — this is an upgrade, not a fresh install)"
else
    log "anti_share_config table is empty — seeding safe defaults for fresh install"
    log "  nft_enabled=0 (no bans), nft_dry_run=1 (extra safety), telegram_enabled=0"
    mysql "$DB_NAME" <<'SQL'
INSERT INTO anti_share_config (
  enabled, window_seconds, learning_days, retention_days,
  trusted_recent_days, trust_decay_per_day, score_decay_clean,
  score_plus1, score_plus2, score_plus3,
  suspect_score, warn_score, block_score,
  severe_new_ip_threshold, severe_traffic_ratio,
  ban_seconds, telegram_enabled, nft_enabled, nft_dry_run,
  nft_helper, scan_limit, current_ip_snapshot_limit, service_name,
  created_at
)
VALUES (
  1, 120, 7, 45,
  7, 0.15, 0.25,
  0.25, 0.50, 1.00,
  0.50, 0.75, 1.00,
  3, 5.0,
  3600, 0, 0, 1,
  '/opt/hiddify-manager/common/hiddify-antishare-nft.sh', 1000, 32, 'hiddify-anti-share',
  NOW()
);
SQL
fi

cfg_count="$(mysql "$DB_NAME" -N -B -e "SELECT COUNT(*) FROM anti_share_config;" 2>/dev/null | head -n1 || echo '0')"
[[ "$cfg_count" -ge 1 ]] || die "anti_share_config seed failed: table is still empty"
log "anti_share_config rows: $cfg_count"

# --- Step 10: Set commercial_antishare_installed=1 in bool_config ---
# commercial_antishare_installed.type == bool in ConfigEnum.
# On production servers with db_version < 136, the bool_config.key ENUM may not yet
# include 'commercial_antishare_installed' (added in panel _v136 migration).
# Check INFORMATION_SCHEMA first; skip INSERT if key not in ENUM to avoid ERROR 1265.
# antishare_enabled() will fall back to manifest file in that case.
log "Setting commercial_antishare_installed marker"
enum_has_antishare_key="$(mysql "$DB_NAME" -N -B -e \
    "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
     WHERE TABLE_SCHEMA='$DB_NAME' AND TABLE_NAME='bool_config'
       AND COLUMN_NAME='key' AND COLUMN_TYPE LIKE '%commercial_antishare_installed%';" \
    2>/dev/null | head -1 || echo 0)"

if [[ "${enum_has_antishare_key:-0}" -ge 1 ]]; then
    mysql "$DB_NAME" <<'SQL'
INSERT INTO bool_config (child_id, `key`, value)
VALUES (0, 'commercial_antishare_installed', 1)
ON DUPLICATE KEY UPDATE value = 1;
SQL
    log "commercial_antishare_installed=1 set in bool_config"
else
    log "commercial_antishare_installed not in bool_config ENUM (db_version < 136 schema)"
    log "antishare_enabled() will use antishare-addon.manifest fallback — this is expected on upgrade"
fi

installed_val="$(mysql "$DB_NAME" -N -B \
    -e "SELECT value FROM bool_config WHERE child_id=0 AND \`key\`='commercial_antishare_installed';" \
    2>/dev/null | head -n1 || echo '')"
if [[ "${enum_has_antishare_key:-0}" -ge 1 ]]; then
    [[ "$installed_val" == "1" ]] \
        || die "commercial_antishare_installed not set to 1 after migration. Got: '$installed_val'"
    log "commercial_antishare_installed=1 confirmed"
else
    log "commercial_antishare_installed in bool_config: '$installed_val' (manifest fallback active)"
fi

# --- Step 11: Remove stale str_config entry for commercial_antishare_installed ---
# commercial_antishare_installed is a bool-typed ConfigEnum key.
# get_hconfigs() reads it ONLY from bool_config. A str_config row is ignored by the
# panel but confuses diagnostics. Clean it up idempotently.
log "Removing stale str_config.commercial_antishare_installed if present"
mysql "$DB_NAME" <<'SQL'
DELETE FROM str_config
WHERE child_id=0 AND `key`='commercial_antishare_installed';
SQL
stale_count="$(mysql "$DB_NAME" -N -B \
    -e "SELECT COUNT(*) FROM str_config WHERE child_id=0 AND \`key\`='commercial_antishare_installed';" \
    2>/dev/null | head -n1 || echo '-1')"
[[ "$stale_count" == "0" ]] \
    || die "str_config.commercial_antishare_installed still present after cleanup. Got: $stale_count rows"
log "str_config.commercial_antishare_installed cleaned (was stale bool key in wrong table)"

log "Anti-share DB migration completed successfully"
