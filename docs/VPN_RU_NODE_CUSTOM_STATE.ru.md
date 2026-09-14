# VPN Ru Node: custom-состояние Hiddify

Дата фиксации: 2026-09-14. Hiddify: 12.0.0. Это не stock-инсталляция.

## Источник истины

Upstream Hiddify используется только как справочная база. Источник истины — текущая реализация VPN Ru Node. Перед любым переносом патча нужно сверять живой runtime, локальные patches и этот репозиторий.

На production существуют две копии Python-пакета:

- `/opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel/` — фактический runtime `hiddify-panel.service`;
- `/opt/hiddify-manager/hiddify-panel/src/hiddifypanel/` — справочный checkout, живым процессом не импортируется.

Патчить `src/` вместо `site-packages` бессмысленно. После обновления версия Python и путь venv могут измениться, поэтому runtime-путь всегда нужно определять заново через systemd, `pip show hiddifypanel` и `hiddifypanel.__file__`.

## Подтверждённые изменения и находки

### Gcore CDN: mixed-case TLS

`ConfigEnum.tls_mixed_case` штатно изменён с `true` на `false`. Gcore отклонял TLS ClientHello с randomized/mixed-case SNI (`tls: internal error`). Изолированный A/B подтвердил причинность. Настройка имеет `ApplyMode.nothing`, поэтому restart/reload не требовался.

Глобальное отключение касается CDN-профилей VLESS/Trojan/VMess. Direct Reality, Direct WS/XHTTP, Hysteria2, TUIC, Mieru и Naive этим код-путём не затрагиваются.

### Hiddify link generator: сериализация `headers`

В Hiddify 12.0.0 generic-ветка `hiddifypanel/hutils/proxy/xray.py` передаёт dict/list из `Proxy.params` в `urlencode()` как Python `str()`/repr. В `vless://` получается значение с одинарными кавычками, которое не является JSON. WS, gRPC и HTTPUpgrade могут получить некорректный `headers=`. XHTTP использует отдельную JSON-ветку и этим конкретным дефектом не затронут.

Минимальный фикс сохранён в `patches/hiddifypanel-12.0.0-xray-query-json.patch`. Он сериализует только dict/list через `json.dumps()` и не меняет scalar-параметры. Патч нельзя применять вслепую: сначала выполнить `tools/check-hiddifypanel-xray-headers-json.py` на фактическом runtime-файле.

Статус production на момент фиксации: `tls_mixed_case=false` подтверждено; применение headers-патча к реально импортируемому `site-packages` не подтверждено. Ранняя правка справочной `src/`-копии была отменена.

### Проверка `create_app()`

Ранний `ImportError` для `commercial.capabilities` оказался артефактом ручного `sys.path.insert(0, 'src')`. При запуске из реального venv/cwd без подмены `sys.path` пакет импортируется из `site-packages`, `capabilities.py` присутствует, `create_app()` успешно создаётся. Вывод «следующий restart панели гарантированно упадёт» был ложной тревогой.

### CDN transports

- Gcore XHTTP H2 через штатный inbound подтверждён реальным клиентским запросом.
- Direct WS через тот же origin/inbound подтверждён.
- Gcore WS на рабочем path возвращал `404` при WebSocket handshake; это локализует отказ в CDN WS path/configuration, но HTTP `404` на `/` сам по себе не означает outage CDN.
- Selectel использует отдельный custom XHTTP packet-up inbound и HAProxy backend. Его серверные template/runtime-файлы не входят в этот коммит и не должны восстанавливаться из предположений или upstream.

## После Hiddify update

1. Определить новый runtime package path.
2. Запустить checker на реальном `hutils/proxy/xray.py`.
3. Если результат `FIX_PRESENT` — upstream уже исправлен, локальный patch не применять.
4. Если `BUG_PRESENT` — сверить контекст и применить минимальный patch с backup.
5. Если `UNKNOWN_LAYOUT` — остановиться: версия конфликтует с patch, нужен ручной review.
6. Повторно проверить `tls_mixed_case=false` и реальные generated WS/gRPC/HTTPUpgrade links.
7. Выполнить smoke direct/CDN transports; Git-коммит не считать доказательством production.

Секреты, UUID, приватные ключи и реальные custom paths в документации и Git не сохраняются.
