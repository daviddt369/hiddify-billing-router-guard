## Business Installer

Standalone installer for the Business addon only.

Included module scope:
- `Telegram`
- `YooKassa`
- `Тарифы`

Not included:
- `Маршрутизация`
- `Антишеринг`
- `hiddify-cli` / proxy-status repair

## Expected behavior

`install-business.sh` is verbose and prints each major step in the terminal.

It does not ask for:
- Telegram bot token
- YooKassa `shop_id` / secret
- webhook secret
- owner uid

Those values must be configured later through the panel UI.

On a clean VM these warnings are expected and do not mean install failure:
- `Telegram bot token is not configured`
- `Anti-share admin views disabled: optional module missing`

## Full runbook (verified on clean Hiddify VM)

```bash
cd /root/hiddify-billing-router-guard/release/service-tools
sudo bash audit-hiddify-cli.sh
sudo bash stabilize-hiddify-cli.sh
sudo bash smoke-hiddify-cli.sh

cd /root/hiddify-billing-router-guard/release/business-installer
sudo bash install-business.sh && sudo bash smoke-business.sh

cd /root/hiddify-billing-router-guard/release/service-tools
sudo bash stabilize-hiddify-cli.sh
sudo bash smoke-hiddify-cli.sh

cd /root/hiddify-billing-router-guard/release/business-installer
sudo bash smoke-business.sh
```

Note on `hiddify-cli`: the upstream service enters a restart storm on clean Hiddify before and after business install. This is an upstream bug unrelated to the business installer. `stabilize-hiddify-cli.sh` intentionally disables the service (`HIDDIFY_CLI_DEGRADED_EXPECTED`). The panel and business addon work correctly without it.

Expected successful terminal markers:
- `Business addon install OK`
- `smoke-business OK`

## Package contents

- `install-business.sh`
- `smoke-business.sh`
- `rollback-business.sh`
- `collect-business-diagnostics.sh`
- `common.sh`
- `payload/`
- `SHA256SUMS.txt`

## Как активировать Telegram-администратора

После установки:

1. Зайдите в `Бизнес -> Telegram`.
2. Укажите `Telegram Bot Token`.
3. Нажмите сохранить.
4. Посмотрите команду активации.
5. Отправьте эту команду боту из Telegram-аккаунта администратора.

Команду можно посмотреть и на сервере:

```bash
sudo cat /opt/hiddify-manager/business-addon-secrets/telegram-owner-activation.txt
```

Installer сохраняет root-only файл:

- путь: `/opt/hiddify-manager/business-addon-secrets/telegram-owner-activation.txt`
- владелец: `root:root`
- права: `600`
- формат: `/start admin_<owner_uuid>`

## Rollback

```bash
cd /root/hiddify-billing-router-guard/release/business-installer
sudo bash rollback-business.sh
```

To restore DB dump as well:

```bash
sudo bash rollback-business.sh --restore-db
```

## Diagnostics

```bash
cd /root/hiddify-billing-router-guard/release/business-installer
sudo bash collect-business-diagnostics.sh
```

## Notes

- Runtime path is autodetected under `site-packages/hiddifypanel`.
- Backups are written under `/opt/hiddify-manager/business-installer-backups/`.
- Installer writes `installed-files.txt` and `created-files.txt`.
- Tariffs DB migration writes a DB dump and schema snapshots before changes.
- Common runtime dependencies required by installed business files are bundled in the payload.
- `hiddify-cli` baseline and restart-storm handling are intentionally outside this package and belong to `release/service-tools/`.
