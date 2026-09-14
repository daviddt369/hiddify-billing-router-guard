# Disaster recovery: переезд на новый сервер

Что делать, если хостинг-провайдер заблокировал/потерял текущий VPS и нужно
поднять всё заново на другом сервере максимально быстро.

## Статус проверки

**Отрепетировано end-to-end на реальном одноразовом VPS 2026-09-14**:
`bootstrap.sh` (чистый Ubuntu 24.04 → Hiddify 12.0.0 + все три аддона +
headers-патч) → `restore.sh` (реальный бандл `snapshot.sh` с прода: полная
БД с реальными пользователями, секреты, WireGuard-ключи, сертификаты,
Selectel-шаблоны) → сервисы подняты, прямой smoke-test HTTP 200, патч
подтверждён активным в живом файле. Подробности и найденные/исправленные
баги — в `AGENT_COORDINATION_VPN.md`.

## Ограничения (читать перед использованием)

- CDN-путь (через Gcore/Selectel) в репетиции не проверялся — только прямой
  (origin) smoke-test. Шаг 3 из чеклиста ниже по-прежнему не автоматизирован
  и не отрепетирован.
- DNS-записи и настройки CDN-ресурсов (Gcore/Selectel) физически не могут
  быть автоматизированы отсюда — это состояние живёт в личных кабинетах
  провайдеров, не на VPS. Всегда остаётся ручной шаг.
- `snapshot.sh` — это снимок на момент запуска. Он устаревает по мере роста
  базы. Держать бэкап свежим — ответственность оператора (запускать регулярно
  и хранить копию вне этого сервера).
- Заливка в offsite (Selectel S3) теперь автоматизирована (`offsite-sync.sh`
  + systemd-таймер), но сам этот кусок ещё не отрепетирован полным циклом
  "автоматический снимок на проде → офсайт → restore на чистом VPS
  --from-offsite" — только вручную по частям 14.09.2026.

## Обычный сценарий: старый сервер жив, готовим страховку

**Вручную, разово:**
```bash
sudo bash disaster-recovery/snapshot.sh
# Путь вида /root/dr-snapshots/<timestamp>/ — скопируйте за пределы сервера.
```

**Автоматически, по расписанию (рекомендуется):**
```bash
# Настройка (один раз):
sudo mkdir -p /etc/vpn-ru-node
sudo nano /etc/vpn-ru-node/backup.env   # см. "Настройка offsite" ниже
sudo cp disaster-recovery/systemd/hiddify-dr-backup.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hiddify-dr-backup.timer
# По умолчанию: пн и чт в 03:00. Проверить: systemctl list-timers | grep dr-backup
```
`scheduled-backup.sh` (которую запускает таймер) = `snapshot.sh` +
`offsite-sync.sh`: снимает бэкап, шифрует `age` (публичным ключом — сервер
НЕ может расшифровать то, что сам зашифровал), заливает в Selectel S3,
чистит старые версии и локально, и в бакете (по умолчанию 3 локально / 8 в
бакете).

## Настройка offsite (Selectel S3 + age-шифрование)

`/etc/vpn-ru-node/backup.env` (root-only, 0600, никогда не в git):
```
SELECTEL_S3_ACCESS_KEY=...
SELECTEL_S3_SECRET_KEY=...
SELECTEL_S3_ENDPOINT=https://s3.<region>.storage.selcloud.ru
SELECTEL_S3_BUCKET=...
DR_AGE_PUBLIC_KEY=age1...
```
Ключевая пара `age` генерируется один раз (`age-keygen`), **приватный ключ
никогда не хранится на сервере** — только у оператора. Без него зашифрованные
офсайт-бэкапы не расшифровать вообще никому, включая владельца сервера при
его компрометации.

## Аварийный сценарий: переезд на новый сервер

```bash
# 1. На НОВОМ, чистом Ubuntu 22.04/24.04 VPS, от root:
git clone <url-этого-репозитория>
cd hiddify-billing-router-guard
sudo bash disaster-recovery/bootstrap.sh
# Ставит: Hiddify Manager 12.0.0 (pinned) → business/routing/antishare
# аддоны → headers-патч xray.py (через уже существующий чекер/патч) →
# Selectel CDN-шаблоны (если задан SELECTEL_XHTTP_PATH, иначе пропускается
# — restore.sh поставит их сам из бандла).
# После этого шага сервис УЖЕ запущен, но на ПУСТОЙ/дефолтной БД — это
# ожидаемо, следующий шаг её заменит.

# 2а. Восстановление из локального бандла:
sudo bash disaster-recovery/restore.sh /path/to/snapshot-bundle/

# 2б. ИЛИ восстановление прямо из Selectel (нужен backup.env на этом сервере
#     — скопировать со старого сервера/сделать заново — и файл с приватным
#     age-ключом, тот самый, что хранится отдельно у оператора):
sudo bash disaster-recovery/restore.sh --from-offsite /path/to/age-private-key.txt

# Restore.sh (в любом из двух вариантов): останавливает сервисы → заменяет
# БД → восстанавливает секреты/сертификаты/WireGuard-ключи → ставит
# Selectel-шаблоны с реальным path из бандла → перегенерирует живые
# Xray/HAProxy конфиги (apply_configs.sh) → flush Redis → проверяет
# tls_mixed_case=false → запускает сервисы → прямой (не через CDN) smoke-test.

# 3. РУКАМИ, не автоматизируется:
#    - обновить DNS-записи всех доменов на новый IP;
#    - обновить origin/IP CDN-ресурсов в панелях Gcore/Selectel;
#    - повторить smoke-test уже ЧЕРЕЗ CDN после того как DNS/CDN обновились.
```

## Что внутри

| Файл | Роль |
|---|---|
| `common-dr.sh` | Общие функции (детект runtime-пути hiddifypanel, парсинг DB URI, health-check сервисов) — переиспользует те же паттерны, что и `release/business-installer/common.sh`. |
| `snapshot.sh` | Снимает с живого сервера: полный дамп БД, Selectel CDN-шаблоны, секреты панели, WireGuard-ключи (найденные через `file:`-ссылки в БД), TLS-сертификаты всех доменов. |
| `bootstrap.sh` | На чистом VPS: ставит Hiddify 12.0.0 + аддоны + headers-патч + (опционально) Selectel-шаблоны. Не трогает данные. |
| `restore.sh` | Восстанавливает данные из бандла `snapshot.sh` (локального или из Selectel через `--from-offsite`) на сервер после `bootstrap.sh`. |
| `offsite-sync.sh` | Шифрует последний снимок (`age`) и заливает в Selectel S3, чистит старые версии локально и в бакете. |
| `scheduled-backup.sh` | `snapshot.sh` + `offsite-sync.sh` вместе — то, что реально запускает systemd-таймер. |
| `systemd/hiddify-dr-backup.{service,timer}` | Автозапуск `scheduled-backup.sh` 2 раза в неделю. |
| `selectel-template/` | Параметризованные версии Selectel CDN-файлов — реальный секретный путь НЕ хранится в git, только плейсхолдер `__SELECTEL_XHTTP_PATH__`. |

## Почему секретный path не в git

Оригинальные файлы Selectel-интеграции содержат захардкоженный секретный
путь. В этом репозитории он заменён на плейсхолдер — `restore.sh`
подставляет реальное значение из бандла `snapshot.sh` (который сам никогда
не коммитится, см. `.gitignore`), а не из git. Так структура шаблонов может
безопасно жить в публичном репозитории.
