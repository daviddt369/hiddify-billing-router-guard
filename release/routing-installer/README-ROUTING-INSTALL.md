## Routing Installer

Standalone installer for the Routing addon. Installs after the Business addon.

## Install order / Dependencies

### Recommended order

```
clean Hiddify -> business -> routing -> antishare
```

### Supported orders

| Order | Status |
|---|---|
| clean Hiddify -> business | OK |
| business -> routing | OK |
| business -> antishare | OK |
| business -> routing -> antishare | OK (recommended full stack) |

### Not fully supported

| Order | Status | Reason |
|---|---|---|
| business -> antishare -> routing | not validated | routing smoke hard-fails if AntiShareAdmin is already registered; future compatibility fix required |

### Unsupported

| Order | Status | Reason |
|---|---|---|
| clean Hiddify -> routing | FAIL | assert_business_installed() — business manifest missing |
| clean Hiddify -> antishare | FAIL | antishare depends on business layer |
| routing -> business | FAIL | routing-installer assert fails, business-installer would overwrite shared files |
| antishare -> business | FAIL | business-installer overwrites __init__.py, erasing antishare patches |
| repeated business-installer over routing/antishare | FAIL | business-installer overwrites business-settings.html, admin-layout.html, __init__.py — destroys routing/antishare patch markers |

### Why routing hard-depends on business

routing-installer requires business to be installed first because:

1. `business-addon.manifest` — checked by `assert_business_installed()`, hard FAIL if missing
2. `panel/admin/__init__.py` — business installs this file; routing patches it using `from .AntiShareAdmin import AntiShareAdmin` as insertion point
3. `business-settings.html` — business installs this template; routing patches it in 5 steps
4. `templates/admin-layout.html` — business installs this; routing patches sidebar section
5. `BusinessAdmin.py` / `RoutingAdmin` — routing's RoutingAdmin inherits from business layer's RoutingAdmin class
6. `capabilities.py`, `config_enum.py`, `init_db.py` — installed by business layer, used by routing models and hconfigs

### Future: antishare compatibility

If `business -> antishare -> routing` order must be supported, the following changes are needed (not in scope now):

- `smoke-routing.sh`: do not hard-fail on `AntiShareAdmin:index` if `anti-share-addon.manifest` already exists
- `routing-installer`: add explicit warning (not die) if antishare is already installed
- README of all installers: document supported install order

## Архитектура трафика

Routing module НЕ заменяет основной Hiddify core. Он ставит второй routing layer поверх него.

```
Клиент (VPN/proxy подключение)
         |
         v
Штатный Hiddify Xray / Singbox  [основной core]
  routing rules направляют трафик в:
  outbound "commercial-local-router" — SOCKS5, 127.0.0.1:20808
         |
         v
xray-router.service  [второй routing layer]
  inbound: "from-hiddify", SOCKS5, 127.0.0.1:20808
  ├── direct-ru    → freedom (локальный трафик напрямую)
  ├── upstream-{id} → VLESS / Trojan / WireGuard (внешняя нода)
  └── block        → blackhole (госсайты, BitTorrent)
```

## Scope

Included:
- `commercial_routing.py` — логика маршрутизации, postfix pipeline
- `router_core.py` — рендеринг конфига xray-router
- `commercial_routing_custom_rule` DB model — пользовательские правила
- `commercial_routing_upstream` DB model — upstream ноды (Stage 2A+)
- Jinja2 шаблоны `03_routing.json.j2`, `06_outbounds.json.j2` — xray и singbox
- `xray-router.service` — дополнительный xray-router service
- `commercial-routing-apply` команда в commander.py (guard-marker patch)
- sudoers rule для panel user

Not included:
- Business (Telegram, YooKassa, Тарифы) — ставится отдельно
- Антишеринг — отдельный этап
- hiddify-cli repair — service-tools

## Prerequisite

Business addon must be installed first:
```bash
ls /opt/hiddify-manager/business-addon.manifest
```

## Full runbook (clean VM, business already installed)

```bash
# 1. Baseline hiddify-cli check (optional)
cd /home/texas/lab-work/release/service-tools
sudo bash smoke-hiddify-cli.sh
# Expected: HIDDIFY_CLI_DEGRADED_EXPECTED

# 2. Install routing
cd /home/texas/lab-work/release/routing-installer
sudo bash install-routing.sh && sudo bash smoke-routing.sh

# Note: install-routing.sh does NOT run apply_configs.sh.
# hiddify-cli stabilize is only needed if you manually ran apply_configs.sh.
# After a normal install-routing.sh run, step 3 is not required.

# 3. Business regression check
cd /home/texas/lab-work/release/business-installer
sudo bash smoke-business.sh
# Expected: smoke-business OK

# 4. Final routing smoke
cd /home/texas/lab-work/release/routing-installer
sudo bash smoke-routing.sh
# Expected: smoke-routing OK
```

## Expected warnings (norm, not failures)

- `Telegram bot token is not configured`
- `Anti-share admin views disabled: optional module missing`
- `xray-router.service inactive` — expected on clean VM until upstream node is configured
- `commercial_routing_enable=0: routing installed but NOT active` — expected until manually activated (see below)

## Architecture after install

Installer deploys the routing layer but does NOT activate it automatically.
Activation requires enabling routing in UI and regenerating main Hiddify configs:

```
After install (inactive):
  Main Hiddify Xray/Singbox → freedom / blocked (no router-core involvement)
  xray-router.service → running, listening on 20808, but receives NO traffic

After activation:
  Main Hiddify Xray/Singbox → commercial-local-router (SOCKS5 127.0.0.1:20808)
  xray-router.service → receives all traffic, routes:
    Local geoip/domains → direct-ru (freedom)
    Global traffic   → upstream-balancer (VLESS/Trojan/WireGuard)
    Gov sites        → block (blackhole)
```

## Как включить routing (activation runbook)

### Шаг 1: Добавить upstream ноды

```
https://<your-domain>/<proxy_path>/admin/routing-admin/upstreams/
```

Добавить минимум одну ноду (VLESS / Trojan / WireGuard). Применить routing:

```bash
sudo -n /opt/hiddify-manager/common/commander.py commercial-routing-apply
sudo /usr/bin/xray run -test -config /etc/xray-router/config.json
```

### Шаг 2: Включить routing в UI

```
https://<your-domain>/<proxy_path>/admin/routing-admin/
```

Включить `commercial_routing_enable`. Убедиться:
- `commercial_domestic_policy = send_to_router`
- `commercial_apply_to_xray = 1`
- `commercial_apply_to_singbox = 1`
- `commercial_legacy_geosite_to_router = 1` (geosite:google/netflix через router)

### Шаг 3: Регенерировать main Hiddify конфиги

```bash
sudo /opt/hiddify-manager/apply_configs.sh --no-gui
```

Или через панель: Settings → Apply configs.

**Внимание:** `apply_configs.sh` делает внешние сетевые вызовы. Если сервер в ограниченной сети — запускать только когда есть соединение.

### Шаг 4: Проверить результат

```bash
# commercial-local-router должен появиться в main Xray outbounds
grep 'commercial-local-router' /opt/hiddify-manager/xray/configs/06_outbounds.json

# geoip:ru/tld-ru/geosite должны идти в commercial-local-router
grep 'commercial-local-router' /opt/hiddify-manager/xray/configs/03_routing.json

# singbox final должен вести в commercial-local-router
python3 -c "import json; d=json.load(open('/opt/hiddify-manager/singbox/configs/03_routing.json')); print('final:', d['route']['final'])"

# xray-router слушает
sudo ss -lntp | grep ':20808'

# smoke после активации
cd ~/lab-work/release/routing-installer
sudo bash smoke-routing.sh
```

## Configure upstream node (after install)

1. Зайдите в панель управления upstream-нодами: `/admin/routing-admin/upstreams/`
2. Нажмите «+ Добавить» и выберите тип туннеля (VLESS / Trojan / WireGuard)
3. Укажите данные подключения (URI или endpoint)
4. Сохраните и запустите apply: `sudo -n /opt/hiddify-manager/common/commander.py commercial-routing-apply`

## Stage Routing-2E: Rule Sources

### Управление источниками правил

```
https://<your-domain>/<proxy_path>/admin/routing-admin/rule-sources/
```

Добавить источник → выбрать тип (Текст / URL / Локальный файл) → выбрать семейство (Домены / Подсети) →
нажать **Preview** → убедиться в корректности → нажать **Import** → нажать **Применить** (commander.py).

### Форматы входных данных

**plain_text** (один элемент на строку):
```
# Комментарии (#, //) игнорируются

# Домены (семейство "domain"):
mediaserv.site          → domain_suffix (то же что .mediaserv.site)
.sub.mediaserv.site     → domain_suffix
full:api.telegram.org   → domain_exact
regexp:.*\.ru$          → domain_regex

# Подсети (семейство "subnet"):
1.2.3.4                 → ip
10.0.0.0/8              → cidr
ip:5.6.7.8              → ip
cidr:192.168.0.0/16     → cidr
```

**sing_box_source_json**: JSON-формат sing-box с полями `domain_suffix`, `domain`, `domain_regex`, `ip_cidr`.

**sing_box_binary_srs**: не поддержан в Stage 2E (заглушка).

### Примеры внешних списков (itdoginfo/allow-domains)

Домены РФ (plain text):
```
https://raw.githubusercontent.com/itdoginfo/allow-domains/main/Russia/inside-raw.lst
```

Подсети РФ (CIDR, plain text):
```
https://raw.githubusercontent.com/itdoginfo/allow-domains/main/Russia/inside-cidr.lst
```

> Эти URL приведены как примеры — встроенных пресетов нет.
> Добавьте вручную в UI → Sources → Добавить источник.

### Локальный файл

Разрешённый каталог: `/opt/hiddify-manager/routing-lists/`

```bash
sudo mkdir -p /opt/hiddify-manager/routing-lists/
sudo nano /opt/hiddify-manager/routing-lists/my-domains.txt
# Добавьте домены по одному на строку
```

### Ограничения Stage 2E

- No auto-refresh (cron) — импорт только вручную через UI
- SRS import not implemented — Stage 2F
- URL — только http/https, без приватных IP, timeout 15s, max 512KB, max 50000 строк

## Stage Routing-1 limitations (resolved in Routing-2)

- Single upstream node only — **Routing-2B/2C** adds multi-upstream CRUD and priority-based selection
- URL/file/text rule sources — resolved in **Stage 2E** (URL import, local file, plain text, sing-box JSON)
- SRS import — still open, **Stage 2F**
- Internal config keys `commercial_de_*` not renamed (kept for backward compat)
- Internal outbound tag `to-de` replaced by `upstream-{id}` in new code — **Routing-2C**

## Stage Routing-2C status: PASSED (2026-05-08)

Validated on live VM with 2 real enabled upstreams (ups_usa VLESS priority=0, ups_de Trojan priority=1):

- install-routing.sh: OK
- smoke-routing.sh: OK, all checks green
- commercial-routing-apply: OK (custom_rules=0 at Stage 2C validation)
- xray run -test: Configuration OK
- Config structure verified:
  - upstream-2 (VLESS), upstream-1 (Trojan), upstream-3 (blackhole) as outbounds
  - routing.balancers present (not top-level): selector [upstream-2, upstream-1]
  - strategy: leastPing
  - final rules use balancerTag: upstream-balancer
  - observatory: subjectSelector [upstream-2, upstream-1], probeInterval 1m
  - no hardcoded to-de in new path
- xray-router.service: active (was inactive before apply)
- Auto-failover: observatory probes upstreams every 1 minute; balancer
  switches to live node automatically within ~1-2 minutes of failure
- split DNS and gov-block postfixes apply correctly with balancerTag

## Final routing release smoke: PASSED (2026-05-08)

Validated after full Stage 2A-2E implementation with rule sources and source_id tracking:

- install-routing.sh: OK (all 13 steps including admin-layout.html sidebar patch)
- smoke-routing.sh: OK, all checks green (Checks 1-13)
- commercial-routing-apply: OK, custom_rules=56
- xray run -test: Configuration OK
- xray-router.service: active/running, NRestarts=0
- 127.0.0.1:20808: LISTEN
- upstreams: ups_usa (VLESS, priority=0), ups_de (Trojan, priority=1), ups_pl (blackhole, priority=2)
- balancer selector: [upstream-2, upstream-1] with leastPing + observatory 1m probes
- rule sources: 4 sources (mu-ru URL/domain, googleapis text/domain, mu-ip text/subnet, youtube URL/domain)
- custom rules: 56 direct_ru rules in xray-router config
- source_id tracking: toggle/delete source cascades to its rules

## Stage Routing-2B status: PASSED (2026-05-08)

Validated on live VM (business + routing-2A pre-installed):

- `RoutingUpstreamAdmin.py` installed, all 7 endpoints registered
- `panel/admin/__init__.py` patched with ROUTING_UPSTREAM_ADMIN guard block
- `business-settings.html` patched with upstream link
- `/upstreams/` list page renders correctly
- `/upstreams/add/` and `/upstreams/<id>/edit/` forms work (csrf_token fix applied)
- Added 2 real upstream nodes (VLESS + Trojan, both on <UPSTREAM_DOMAIN>:443)
- Jinja2 template cache cleared via panel restart after template fix
- Connectivity test via temporary xray instance on port 20809:
  VLESS upstream → IP: <UPSTREAM_IP> Role: External upstream — connection works
- Business and antishare not touched
- Next: Stage 2C — router_core multi-upstream rendering

## Stage Routing-2A status: PASSED (2026-05-08)

Validated on clean VM (business addon pre-installed):

- `commercial_routing_upstream` table created with correct schema
- Schema self-check passed: PRIMARY KEY, UNIQUE KEY uq_upstream_name,
  KEY ix_upstream_enabled, KEY ix_upstream_priority,
  columns last_status / last_error / last_checked_at present
- `db-upstream-count=0` — expected: legacy tunnel_type is test_blackhole, seed skipped
- `import-ok hiddifypanel.models.commercial_routing_upstream` — model loads correctly
- Runtime routing behavior unchanged
- Business and antishare not touched

## Rollback

```bash
cd /home/texas/lab-work/release/routing-installer
sudo bash rollback-routing.sh
```

With DB restore:
```bash
sudo bash rollback-routing.sh --restore-db
```

## Diagnostics

```bash
cd /home/texas/lab-work/release/routing-installer
sudo bash collect-routing-diagnostics.sh
```

## Notes

- Backups written to `/opt/hiddify-manager/routing-installer-backups/`
- Manifest at `/opt/hiddify-manager/routing-addon.manifest`
- commander.py patched with guard markers `# ROUTING_INSTALL_BEGIN` / `# ROUTING_INSTALL_END`
- Rollback removes only the marker block from commander.py
- DB migration: `commercial_routing_custom_rule` table, UNIQUE key uses `normalized_value(255)` prefix (MySQL/MariaDB-safe)
- DB migration: `commercial_routing_upstream` table — upstream nodes with priority, tunnel type, status fields
- Rollback does NOT drop `commercial_routing_upstream` — user data preserved; DB restore only via `--restore-db` from dump
