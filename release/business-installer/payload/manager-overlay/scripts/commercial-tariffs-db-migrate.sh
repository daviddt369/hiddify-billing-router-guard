#!/usr/bin/env bash
set -Eeuo pipefail

DB_NAME="${DB_NAME:-hiddifypanel}"
BACKUP_DIR="${BACKUP_DIR:-}"

die() {
  echo "[commercial-tariffs-db][ERROR] $*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"
}

table_exists() {
  local table="$1"
  [[ -n "$(mysql -D "$DB_NAME" -Nse "SHOW TABLES LIKE '$table';")" ]]
}

column_exists() {
  local table="$1" column="$2"
  [[ -n "$(mysql -D "$DB_NAME" -Nse "SHOW COLUMNS FROM \`$table\` LIKE '$column';")" ]]
}

index_exists() {
  local table="$1" index_name="$2"
  [[ -n "$(mysql -D "$DB_NAME" -Nse "SHOW INDEX FROM \`$table\` WHERE Key_name = '$index_name';")" ]]
}

constraint_exists() {
  local table="$1" constraint_name="$2"
  [[ -n "$(mysql -D "$DB_NAME" -Nse "SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS WHERE TABLE_SCHEMA='$DB_NAME' AND TABLE_NAME='$table' AND CONSTRAINT_NAME='$constraint_name';")" ]]
}

dump_schema_snapshot() {
  local suffix="$1"
  local out="$BACKUP_DIR/db-schema-$suffix.sql"
  {
    echo "-- $DB_NAME schema snapshot: $suffix"
    echo
    mysql -D "$DB_NAME" -Nse "SHOW CREATE TABLE user\G" || true
    echo
    if table_exists commercial_plan; then
      mysql -D "$DB_NAME" -Nse "SHOW CREATE TABLE commercial_plan\G"
    else
      echo "-- commercial_plan missing during snapshot: $suffix"
    fi
    echo
    if table_exists commercial_subscription; then
      mysql -D "$DB_NAME" -Nse "SHOW CREATE TABLE commercial_subscription\G"
    else
      echo "-- commercial_subscription missing during snapshot: $suffix"
    fi
  } > "$out"
}

ensure_backup_material() {
  [[ -n "$BACKUP_DIR" ]] || die "BACKUP_DIR is required"
  mkdir -p "$BACKUP_DIR"
  if [[ ! -f "$BACKUP_DIR/db-dump.sql" ]]; then
    mysqldump --single-transaction --quick --skip-lock-tables "$DB_NAME" > "$BACKUP_DIR/db-dump.sql"
  fi
  dump_schema_snapshot "before"
  cat > "$BACKUP_DIR/db-rollback.txt" <<EOF
Rollback commands:
  mysql $DB_NAME < $BACKUP_DIR/db-dump.sql

Schema snapshot before migration:
  $BACKUP_DIR/db-schema-before.sql
EOF
}

validate_existing_commercial_plan() {
  local required=(id name cycle usage_limit package_days max_ips mode enable is_public price currency payment_provider sort_order note added_by created_at updated_at)
  local col
  for col in "${required[@]}"; do
    column_exists commercial_plan "$col" || die "Existing commercial_plan table is missing column: $col"
  done
}

validate_existing_commercial_subscription() {
  local required=(id user_id plan_id start_date end_date suspended_at canceled_at auto_renew usage_limit package_days max_ips mode billing_amount billing_currency payment_provider external_payment_id note created_by created_at updated_at)
  local col
  for col in "${required[@]}"; do
    column_exists commercial_subscription "$col" || die "Existing commercial_subscription table is missing column: $col"
  done
}

create_commercial_plan_if_needed() {
  if table_exists commercial_plan; then
    echo "[commercial-tariffs-db] commercial_plan exists, validating"
    validate_existing_commercial_plan
    return
  fi
  echo "[commercial-tariffs-db] commercial_plan missing, creating"
  mysql -D "$DB_NAME" <<'SQL'
CREATE TABLE commercial_plan (
  id INT NOT NULL AUTO_INCREMENT,
  name VARCHAR(128) NOT NULL,
  cycle ENUM('daily','weekly','monthly','quarterly','semiannual','yearly','lifetime') NOT NULL DEFAULT 'monthly',
  usage_limit BIGINT NOT NULL DEFAULT 322122547200,
  package_days INT NOT NULL DEFAULT 31,
  max_ips INT NOT NULL DEFAULT 1,
  mode ENUM('no_reset','monthly','weekly','daily') NOT NULL DEFAULT 'monthly',
  enable TINYINT(1) NOT NULL DEFAULT 1,
  is_public TINYINT(1) NOT NULL DEFAULT 1,
  price INT NOT NULL DEFAULT 0,
  currency VARCHAR(8) NOT NULL DEFAULT 'RUB',
  payment_provider ENUM('manual','yookassa') NOT NULL DEFAULT 'yookassa',
  sort_order INT NOT NULL DEFAULT 100,
  note VARCHAR(512) NOT NULL DEFAULT '',
  added_by INT NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY ux_commercial_plan_name (name),
  KEY ix_commercial_plan_added_by (added_by),
  CONSTRAINT fk_commercial_plan_added_by FOREIGN KEY (added_by) REFERENCES admin_user (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
SQL
}

ensure_user_plan_link() {
  if ! column_exists user plan_id; then
    mysql -D "$DB_NAME" -e "ALTER TABLE user ADD COLUMN plan_id INT NULL;"
  fi
  if ! index_exists user ix_user_plan_id; then
    mysql -D "$DB_NAME" -e "ALTER TABLE user ADD INDEX ix_user_plan_id (plan_id);"
  fi
  if ! constraint_exists user fk_user_plan_id; then
    mysql -D "$DB_NAME" -e "ALTER TABLE user ADD CONSTRAINT fk_user_plan_id FOREIGN KEY (plan_id) REFERENCES commercial_plan (id);"
  fi
}

create_commercial_subscription_if_needed() {
  if table_exists commercial_subscription; then
    echo "[commercial-tariffs-db] commercial_subscription exists, validating"
    validate_existing_commercial_subscription
    return
  fi
  echo "[commercial-tariffs-db] commercial_subscription missing, creating"
  mysql -D "$DB_NAME" <<'SQL'
CREATE TABLE commercial_subscription (
  id INT NOT NULL AUTO_INCREMENT,
  user_id INT NOT NULL,
  plan_id INT NULL,
  start_date DATE NULL,
  end_date DATE NULL,
  suspended_at DATETIME NULL,
  canceled_at DATETIME NULL,
  auto_renew TINYINT(1) NOT NULL DEFAULT 0,
  usage_limit BIGINT NOT NULL DEFAULT 0,
  package_days INT NOT NULL DEFAULT 0,
  max_ips INT NOT NULL DEFAULT 1,
  mode ENUM('no_reset','monthly','weekly','daily') NOT NULL DEFAULT 'no_reset',
  billing_amount INT NOT NULL DEFAULT 0,
  billing_currency VARCHAR(8) NOT NULL DEFAULT 'RUB',
  payment_provider ENUM('manual','yookassa') NOT NULL DEFAULT 'yookassa',
  external_payment_id VARCHAR(128) NULL,
  note VARCHAR(512) NOT NULL DEFAULT '',
  created_by INT NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY ux_commercial_subscription_external_payment_id (external_payment_id),
  KEY ix_commercial_subscription_user_id (user_id),
  KEY ix_commercial_subscription_plan_id (plan_id),
  KEY ix_commercial_subscription_created_by (created_by),
  CONSTRAINT fk_commercial_subscription_user FOREIGN KEY (user_id) REFERENCES user (id),
  CONSTRAINT fk_commercial_subscription_plan FOREIGN KEY (plan_id) REFERENCES commercial_plan (id),
  CONSTRAINT fk_commercial_subscription_created_by FOREIGN KEY (created_by) REFERENCES admin_user (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
SQL
}

main() {
  need_cmd mysql
  need_cmd mysqldump
  ensure_backup_material
  create_commercial_plan_if_needed
  ensure_user_plan_link
  create_commercial_subscription_if_needed
  dump_schema_snapshot "after"
  echo "[commercial-tariffs-db] migration completed"
}

main "$@"
