# routing-landing — «Подключение VPN» + импорт маршрутизации RoscomVPN

Статический лендинг для inline-кнопок Telegram-бота. Работает на RU-ноде за HAProxy/nginx Hiddify.
Это **обезличенная копия** исходников: вместо реальных значений стоят `routing.example.com` (адрес лендинга), `panel.example.com` (домен панели, которому лендинг отдаваться не должен)
и `https://pay.example.com/` (ссылка оплаты в `clients.json`). Свои значения задаются в `clients.json` и переменных `ROUTING_HOST` / `ROUTING_GUARD_HOST`.

Боевой адрес: `https://routing.example.com/` (+ короткие `/happ`, `/incy`, `/hiddify` — открывают ту же
страницу, прокрученную к нужному клиенту; на `/happ` и `/incy` ещё и пробуют открыть приложение).

## Как это устроено на ноде

```
клиент ──TLS──> HAProxy :443/:80 (терминирует TLS, crt-dir /opt/hiddify-manager/ssl/)
                  └─ host-map → неизвестный host → nginx h2.sock
                        nginx: server `_` (Hiddify, conf.d/xray-base.conf)   <- сервер ПО УМОЛЧАНИЮ
                               server routing.example.com (conf.d/zz-routing-links.conf) <- наш
                                 └─ root /var/lib/routing-links/www/index.html
```

| Что | Где на ноде |
|---|---|
| код, `clients.json` (root, read-only для сервиса) | `/opt/routing-links/` |
| страница, `last-good/`, `dynamic-links.json`, `status.json` (пишет user `nginx`) | `/var/lib/routing-links/` |
| мастер-копия сертификата (root, 700) | `/var/lib/routing-links-tls/` |
| nginx-блок | `/opt/hiddify-manager/nginx/conf.d/zz-routing-links.conf` |
| копия сертификата для HAProxy | `/opt/hiddify-manager/ssl/routing.example.com.{crt,crt.key}` |
| keep-marker (см. «Патч Hiddify») | `/opt/routing-links/keep-ssl/routing.example.com` |
| юниты | `/etc/systemd/system/routing-links-{update,cert}.{service,timer}` |

* `routing-links-update.timer` — каждые 6 ч: качает `HAPP/DEFAULT.DEEPLINK` и `INCY/DEFAULT.DEEPLINK`
  (raw.githubusercontent.com/hydraponique/roscomvpn-routing), валидирует (строгий regex + base64 → JSON с правилами),
  собирает страницу `build.py` и **атомарно** подменяет `index.html`. Любой сбой → остаётся последняя рабочая
  страница (rc 1 = не скачалось, rc 2 = не прошло валидацию). Deeplink подставляется байт-в-байт.
  Ссылки на файлы INCY (Windows/macOS/APK) подбирает `resolve_links.py` через GitHub API; только URL вида
  `https://github.com/INCY-DEV/incy-platforms/releases/download/…`.
* `routing-links-cert.timer` — каждые 5 мин: (1) сторож `guard_check`, (2) раз в сутки `acme.sh --renew`,
  (3) `cert_sync` — вернуть сертификат в `ssl/`, если его там нет, и штатно `systemctl reload hiddify-haproxy`.

## ⚠ Грабли, на которых уже обожглись (2026-10-09)

1. **Порядок файлов nginx.** Запрос с неизвестным `Host` уходит на ПЕРВЫЙ `server` блок сокета. Первая версия
   файла называлась `routing-links.conf`, грузилась раньше `xray-base.conf`, стала сервером по умолчанию и
   ~22 секунды (10:14:50–10:15:12 МСК) отдавала лендинг вместо панели/подписок/обманки на всех остальных доменах.
   Откачено сразу. Теперь файл называется **`zz-…`** (грузится последним), деплой сам проверяет порядок через
   `nginx -T` и откатывается при любом расхождении, а `guard_check` раз в 5 мин проверяет, что `panel.example.com/happ`
   не отдаёт нашу страницу (маркер `id="copy-url"`), и при утечке сам выключает блок. **Не переименовывать файл.**
2. **Hiddify удаляет «чужие» сертификаты ежеминутно.** `install.sh apply_users` (панель запускает его каждую минуту)
   вызывает `common/replace_variables.sh`, а тот удаляет из `ssl/` любой `*.crt`, чьего домена нет в БД Hiddify.
   Решение — одна строка патча (ниже). Без неё `cert_sync` возвращал бы файл и перезагружал HAProxy каждые 5 минут;
   от шторма защищает тормоз: повторная установка не чаще раза в 30 мин (`ROUTING_REINSTALL_BACKOFF`), иначе WARN в журнале.
3. Домен **не добавляем в БД Hiddify**: полный apply делает `stop/start` HAProxy (обрыв всех пользователей), а сам
   домен попал бы в генерацию подписок.

## Патч Hiddify (локальный, откатываемый)

`/opt/hiddify-manager/common/replace_variables.sh`, ровно одна строка (цикл чистки `ssl/`):

```diff
-    if [[ ! " ${domains[@]} " =~ " ${d} " ]]; then
+    if [[ ! " ${domains[@]} " =~ " ${d} " ]] && [ ! -e "/opt/routing-links/keep-ssl/${d}" ]; then  # custom: keep-marker (routing-links)
```
Бэкап: `/opt/hiddify-manager/common/.claude-backups/replace_variables.sh.bak-20261009-102058`.
Автообновление Hiddify отключено, поэтому патч не затрётся сам; при ручном апгрейде Hiddify — перепроверить.

## Содержимое страницы (clients.json)

* Раздел 3 «Маршрутизация RoscomVPN»: три одинаковые плашки (Hiddify — метка «Импорт не нужен», Happ/INCY — кнопка импорта),
  затем блок «Важно: после импорта выберите профиль в настройках» с шагами для Happ и INCY.
  Источники шагов — официальная документация: Happ (`happ.su/main/ru/dev-docs/routing`: профиль привязан к ПОДПИСКЕ,
  изменения применяются только после переподключения, красный «!» = геофайлы не загрузились за 3 мин) и INCY
  (`docs.incy.cc/routing/`). Названия пунктов меню в самих приложениях не сверялись — на странице есть оговорка.
* «Регион США» (адреса, шаги, штаты без налога) виден только на iPhone/iPad (`html[data-plat=ios]`, на других платформах
  скрыт; без JS виден всем). Сторонняя ссылка на «генератор адресов» из исходного текста намеренно не включена.
* Оплата: заголовок + крупная кнопка внизу страницы (`payment` в clients.json).
* Правка текстов: `clients.json` → прогнать тесты → залить `build.py`/`clients.json` в `/opt/routing-links/` →
  `systemctl start routing-links-update.service`. Перед заливкой делать копию в `/opt/routing-links/backup-<дата>/`.

## Эксплуатация

```bash
# принудительно обновить страницу сейчас
systemctl start routing-links-update.service && journalctl -u routing-links-update.service -n 5 -o cat
# текст инструкции / ссылки на клиентов: поправить clients.json локально, прогнать тесты, залить и пересобрать
scp clients.json root@NODE:/opt/routing-links/clients.json && ssh root@NODE systemctl start routing-links-update.service
# состояние
systemctl list-timers 'routing-links-*'; cat /var/lib/routing-links/status.json
```
Сертификат первый раз: `ROUTING_HOST=routing.example.com /opt/routing-links/cert-issue.sh` (требует A-запись на ноду).
Файлы **писать с LF** (деплой-скрипты и shebang ломаются на CRLF; проверять побайтово, а не `grep $'\r'` в Git Bash).

## Откат целиком

```bash
mv /opt/hiddify-manager/nginx/conf.d/zz-routing-links.conf /root/ && nginx -t -c /opt/hiddify-manager/nginx/nginx.conf && systemctl reload hiddify-nginx
systemctl disable --now routing-links-update.timer routing-links-cert.timer
rm -f /opt/hiddify-manager/ssl/routing.example.com.crt* && systemctl reload hiddify-haproxy
# патч Hiddify (необязательно, безвреден): cp /opt/hiddify-manager/common/.claude-backups/replace_variables.sh.bak-20261009-102058 /opt/hiddify-manager/common/replace_variables.sh
```

## Тесты (локально, без обращения к ноде)

```bash
bash tests/test_all.sh <dir с happ.txt и incy.txt> <incy_rel.json>   # сборка, негативные кейсы, update.sh, resolve_links
node tests/test_js.js <path/to/index.html>                           # платформа и автооткрытие по User-Agent
bash tests/test_cert.sh                                              # cert_sync + тормоз штормов (заглушки systemctl/pgrep)
bash tests/test_guard.sh                                             # сторож (заглушки curl/nginx/systemctl)
```
На 2026-10-10: 29 + 20 + 17 + 8 PASS. Реальный импорт на телефоне в тесты не входит — проверяется руками.

## Оплата на странице (ЮKassa)

Внизу страницы - форма оплаты ЮKassa «своя сумма» (тот же набор полей, что даёт кабинет ЮKassa: `cps_phone`, `customerNumber`, `sum`, `shopId`), с плашками тарифов: выбрал плашку - сумма и номер заказа подставились сами, клиент вводит только телефон, привязанный к Telegram. Форма отправляется прямо на `https://yookassa.ru/integration/simplepay/payment`, данные карты на странице не вводятся.

Настройки - `payment.form` в `clients.json`:

| Поле | Что |
|---|---|
| `shop_id` | номер магазина в ЮKassa. `"0"` или пусто = на этом узле формы нет, остаётся обычная кнопка-ссылка (`payment.url`) |
| `plans` | плашки: `name`, `price` (рубли), `note` (подпись); порядок = порядок на странице, первая выбрана по умолчанию |
| `customer_prefix` | начало поля «номер заказчика»; к нему добавляется `<тариф> - <цена> ₽` (так платёж виден в журнале ЮKassa) |
| `phone_placeholder`, `note`, `footer` | тексты |
| `script_url`, `script_sha256` | скрипт формы ЮKassa (`yookassa_construct_form.js`, версии 1.35.0 и 1.37.0 побайтно одинаковы) и его контрольная сумма |

**Скрипт ЮKassa** берётся только если его sha256 равен `script_sha256`: `update.sh` скачивает его, сверяет, хранит последнюю хорошую копию в `last-good/yookassa_form.js`; при несовпадении используется копия, а если её нет - страница остаётся с обычной кнопкой-ссылкой. Скрипт вставляется в страницу как есть (хэш в CSP), внешних скриптов нет; CSP разрешает отправку формы только на `https://yookassa.ru`. Без JavaScript показывается кнопка-ссылка.

**Важно про безопасность:** сумма лежит в самой форме, а не на стороне ЮKassa, и в браузере её можно изменить. Тот, кто зачисляет подписку, должен сверять **фактически оплаченную сумму** из ЮKassa с ценой тарифа, а не верить названию тарифа.

Локальная проверка без настоящей ЮKassa: `build.py ... --yk-script <файл скрипта> --pay-action http://127.0.0.1:8765/pay` и любой локальный сервер, который печатает присланные поля (параметр `--pay-action` только для проверки). Тесты формы (`tests/test_all.sh`, разделы 4b и 5) запускаются, если рядом лежит `tests/fixtures/yookassa_construct_form.js` (скачать по `script_url`, проверить `script_sha256`); в публичную копию он не входит.
