# CP-07 · WireGuard до .190: возвращаем обратный путь (SNAT вместо MASQUERADE)

**Статус: подготовлено, проверено на стенде. На production НЕ применялось. Правила iptables выполняет ВЛАДЕЛЕЦ (моя граница: OS-уровень сервера сам не меняю).**

## 1. Что сломано и почему

Узел `upstream-6` (WG→.190) входит в DE_1 и в AUTO, но **не передаёт данные**: на RU ядерный интерфейс `wg0` имеет RX = 0 пакетов (отправлено сотни пакетов SYN проб), у второго туннеля `wg1` (→.238) RX растёт.

Что видно на .190 (только чтение):

| Факт | Источник |
|---|---|
| Правило `-t nat POSTROUTING -s 10.66.66.2 -o ens3 -j MASQUERADE` пропустило **23 495 пакетов** наружу | `iptables -t nat -L POSTROUTING -nv` |
| Правило возврата `FORWARD -i ens3 -o wg-rude -m conntrack --ctstate RELATED,ESTABLISHED` — **0 пакетов**, а исходящее `wg-rude → ens3` — 23 499 | `iptables -L FORWARD -nv` |
| На `ens3` два адреса: **первый `192.0.2.50/32`**, второй (алиас `ens3:0`) `203.0.113.20/32` — это публичный адрес выхода | `ip -4 addr show dev ens3` |
| Маршрут по умолчанию: `default via 172.16.0.1 dev ens3 src 203.0.113.20 onlink` | `ip route` |
| Рукопожатие WG есть (UDP-ответы WG идут с адреса, на который пришёл пакет); за 4,5 суток с последней загрузки (2026-10-11 23:00) через `wg-rude` прошло лишь 29 542 / 18 235 пакетов, 2,8 МБ / 0,8 МБ — это уровень keepalive, не пользовательский трафик | `ip -s link show wg-rude` |

Механика: цель `MASQUERADE` выбирает исходящий адрес по правилам ядра (`inet_select_addr`) — берёт **первый подходящий адрес интерфейса**, а не `src` из маршрута (его использует только трафик самого сервера). Поэтому пересылаемые пакеты клиента 10.66.66.2 уходят с `192.0.2.50`, ответы на этот адрес не возвращаются, обратного трафика нет. На .238 в такой же конструкции стоит явный `SNAT --to-source 203.0.113.30` (`wg-runl`), и этот туннель работает — исправление приводит .190 к тому же виду.

Подтверждение в лаборатории (стенд, сетевые пространства имён с точным адресным устройством .190; реальный Xray с двумя WG-выходами на ядерных TUN, как на RU): `test/dataplane-masquerade-vs-snat.txt`

| NAT на «.190» | запросы через WG-190 | RX на «wg0» | обратное правило FORWARD | источник SYN на ens3 |
|---|---|---|---|---|
| MASQUERADE | **0 из 3 удались** | 0 пакетов | **0 пакетов** | `10.251.0.2` (первый адрес) |
| `SNAT --to-source <второй адрес>` | **3 из 3** | 15 пакетов | 15 пакетов | `10.251.0.3` (второй адрес) |

**Оговорка (честно):** на самом проде пакет с неверным источником я не снимал (захват на боевом сервере — решение владельца). Причина установлена по совпадению адресной раскладки, счётчиков и воспроизведению в лаборатории. Перед применением можно за 6 секунд подтвердить прямо на сервере: `CAPTURE=1 bash scripts/check-before.sh` покажет, с каким адресом клиентский трафик уходит в `ens3` (пассивное чтение, ничего не пишет). Когда именно туннель перестал работать, неизвестно (журналы хранят только текущую загрузку).

## 2. Точный diff (`/etc/wireguard/wg-rude.conf`, ключи в diff не попадают)

```diff
--- a/etc/wireguard/wg-rude.conf
+++ b/etc/wireguard/wg-rude.conf
@@ -7,7 +7,7 @@
 PostUp = iptables -C INPUT -p udp --dport 51821 -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p udp --dport 51821 -j ACCEPT
 PostUp = iptables -C FORWARD -i wg-rude -o ens3 -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -i wg-rude -o ens3 -j ACCEPT
 PostUp = iptables -C FORWARD -i ens3 -o wg-rude -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -i ens3 -o wg-rude -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
-PostUp = iptables -t nat -C POSTROUTING -s 10.66.66.2/32 -o ens3 -j MASQUERADE 2>/dev/null || iptables -t nat -I POSTROUTING 1 -s 10.66.66.2/32 -o ens3 -j MASQUERADE
+PostUp = iptables -t nat -C POSTROUTING -s 10.66.66.2/32 -o ens3 -j SNAT --to-source 203.0.113.20 2>/dev/null || iptables -t nat -I POSTROUTING 1 -s 10.66.66.2/32 -o ens3 -j SNAT --to-source 203.0.113.20
 PostUp = iptables -I INPUT 1 -i wg-rude -p udp --dport 5300 -j ACCEPT
 PostUp = iptables -I INPUT 1 -i wg-rude -p tcp --dport 5300 -j ACCEPT
 PostUp = iptables -I INPUT 1 -i wg-rude -p udp --dport 53 -j ACCEPT
@@ -15,7 +15,7 @@
 PostDown = iptables -D INPUT -p udp --dport 51821 -j ACCEPT 2>/dev/null || true
 PostDown = iptables -D FORWARD -i wg-rude -o ens3 -j ACCEPT 2>/dev/null || true
 PostDown = iptables -D FORWARD -i ens3 -o wg-rude -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || true
-PostDown = iptables -t nat -D POSTROUTING -s 10.66.66.2/32 -o ens3 -j MASQUERADE 2>/dev/null || true
+PostDown = iptables -t nat -D POSTROUTING -s 10.66.66.2/32 -o ens3 -j SNAT --to-source 203.0.113.20 2>/dev/null || true
 PostDown = iptables -D INPUT -i wg-rude -p udp --dport 5300 -j ACCEPT 2>/dev/null || true
 PostDown = iptables -D INPUT -i wg-rude -p tcp --dport 5300 -j ACCEPT 2>/dev/null || true
 PostDown = iptables -D INPUT -i wg-rude -p udp --dport 53 -j ACCEPT 2>/dev/null || true
```

И то же самое «на лету» (без перезапуска туннеля), порядок «сначала вставить, потом удалить», окна без NAT нет:

```bash
iptables -t nat -I POSTROUTING 1 -s 10.66.66.2/32 -o ens3 -j SNAT --to-source 203.0.113.20
iptables -t nat -D POSTROUTING -s 10.66.66.2/32 -o ens3 -j MASQUERADE
```

## 3. Зависимые службы

| Что | Влияние |
|---|---|
| `wg-quick@wg-rude` | **не перезапускается**; меняется только файл (для следующего старта/перезагрузки) и одно правило iptables |
| таблица `nat` на .190 | одно правило заменяется другим; остальные (Docker, AmneziaWG `amn0`) не трогаются |
| RU: `xray-router`, outbound `upstream-6` | не перезапускается; узел **оживает** — observatory пометит его живым в течение ≈ минуты |
| `unbound` для клиента WG (10.77.77.1:53/5300), AdGuard, Hiddify, HAProxy на .190 | не затрагиваются |

## 4. Риск прерывания подключений

* **Для текущих пользователей — нулевой:** через `upstream-6` сейчас ничего не идёт.
* **Изменение поведения:** узлы DE_1 — 4, 10 (XHTTP/TLS) и 6 (WG). После исправления `leastPing` может отдавать трафик WG-узлу, если у него минимальный замер. WG (UDP, MTU 1280) — тот же тип, что работающий `wg1` на .238; но если нужно вводить узел постепенно, до применения снимите узел 6 со слота 1/AUTO в панели («Применить» перезапустит router ≈1–3 с).
* Старые записи conntrack с адресом `192.0.2.50` у «зависших» потоков истекут сами (до ≈2 минут); новые пробы идут с новыми портами и сразу получают верный адрес.

## 5. Проверка до и после

* До: `bash scripts/check-before.sh` (только чтение; `CAPTURE=1` добавляет 6-секундный пассивный захват): строки MASQUERADE на месте, порядок адресов, счётчики, рукопожатие, включён ли `wg-quick@wg-rude`.
* После: `bash scripts/check-after.sh` (до 150 с ждёт роста счётчика обратного правила FORWARD) + две проверки на RU:
  `ip -s link show wg0` (RX должен расти) и `curl -s -m 8 --interface wg0 https://1.1.1.1/cdn-cgi/trace | grep '^ip='` (ожидается `ip=203.0.113.20`).

## 6. Откат

* Автоматического нет (доступ по SSH не затрагивается, нечем рисковать). Ручной: `bash scripts/rollback.sh [каталог копии]` — возвращает файл из копии (`/root/.claude-backups/cp07-<время>/`) и правило MASQUERADE «на лету» (сначала вставка, затем удаление SNAT).
* Скрипты идемпотентны: повторный `apply.sh` ничего не меняет и не перезаписывает указатель на копию; `rollback.sh` отказывается работать с копией, в которой уже нет MASQUERADE.

## 7. Что проверено на стенде

| Проверка | Результат |
|---|---|
| Лаборатория данных (netns + реальный Xray + ядерные TUN) MASQUERADE против SNAT | см. таблицу выше |
| Скрипты в netns с адресной раскладкой .190, настоящий `wg-quick` и боевой текст `wg-rude.conf` (ключи сгенерированы) | `check-before` → `apply` → результат побайтово равен `patch/wg-rude.conf.after.masked`, права `660 root:root` сохранены → повторный `apply` ничего не делает → `wg-quick down` убирает SNAT, `up` возвращает → `rollback`: файл **побайтово** как исходный, правило MASQUERADE: `test/scripts-in-netns-with-prod-address-layout.txt` |

## 8. Порядок и связь с другими пакетами

* Применять **после CP-05** (fail-closed) и по возможности вместе с мониторингом CP-08: иначе возрождённый WG-узел станет ещё одним невидимым участником балансировки.
* Владелец выполняет `scripts/apply.sh` на .190 от root; копировать на сервер каталоги `scripts/` и `patch/` в `/root/cp07-pkg/`.
