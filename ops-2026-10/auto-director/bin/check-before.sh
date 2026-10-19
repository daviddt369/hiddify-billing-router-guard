#!/bin/bash
# CP-10 · AUTO director · проверка ДО (только чтение). RU, от root. Код возврата 0 = можно применять.
# Переменные для стенда: CP10_SERVICES, CP10_SKIP_INTERNET=1
set -u
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"

echo "1. файлы слоя"
cur=$(sha16 "$LAYER"); curm=$(sha16 "$MLOC")
if [ "$cur" = "$BASE_LAYER" ]; then ok "multiloc_router.py = CP-05 ($cur)"; elif [ "$cur" = "$NEW_LAYER" ]; then ok "multiloc_router.py уже новый ($cur)"; else bad "multiloc_router.py неизвестной версии $cur: применять нельзя"; fi
if [ "$curm" = "$BASE_MLOC" ]; then ok "multiloc.py = боевой ($curm)"; elif [ "$curm" = "$NEW_MLOC" ]; then ok "multiloc.py уже новый ($curm)"; else bad "multiloc.py неизвестной версии $curm: применять нельзя"; fi

echo "2. Xray и службы"
v=$(/usr/bin/xray version 2>/dev/null | head -1); echo "  $v"
case "$v" in *26.7.28*5ca6f4b*) ok "версия Xray та же, на которой проведены тесты (26.7.28)" ;; *) bad "версия Xray другая: тесты CP-10 проводились на 26.7.28 (5ca6f4b)" ;; esac
/usr/bin/xray help api 2>&1 | grep -q "api bo" && ok "в этой сборке Xray есть команда api bo (переопределение балансировщика)" || warn "справка Xray не показывает api bo (на стенде эта же сборка работает)"
for u in ${CP10_SERVICES_BEFORE:-xray-router hiddify-panel hiddify-xray hiddify-haproxy hiddify-singbox}; do [ "$(systemctl is-active $u)" = active ] && ok "$u active" || bad "$u не active"; done

echo "3. router согласован с базой, две страны с живыми узлами"
st=$(cd /opt/hiddify-manager/hiddify-panel && $CTL status 2>&1 | grep '^router:')
case "$st" in *"in sync"*) ok "$st" ;; *) if [ "$cur" = "$NEW_LAYER" ]; then echo "  --    $st (слой уже новый — это ожидаемо до применения)"; else bad "${st:-статус недоступен}"; fi ;;
esac
python3 /dev/stdin <<PY || fail=1
import json, subprocess, sys
r = subprocess.run(["$PYV", "/opt/hiddify-manager/scripts/multiloc_ctl.py", "status", "--json"], capture_output=True, text=True, cwd="/opt/hiddify-manager/hiddify-panel", timeout=120)
st = json.loads(r.stdout[r.stdout.index("{"):])
good = []
for l in st["locations"]:
    if l.get("kind") == "country" and l.get("enabled") and any(n.get("enabled") for n in l.get("nodes", [])):
        good.append((l["slot"], (l.get("iso") or l.get("code") or "").upper(), len([n for n in l["nodes"] if n.get("enabled")])))
print("  страны с узлами:", good)
sys.exit(0 if len(good) == 2 else 1)
PY
[ $? -eq 0 ] && ok "ровно две включённые страны с узлами (директор работает с двумя)" || bad "нужно ровно две включённые страны с узлами"

echo "4. входы router и проба контроллера через них"
for p in 20808 20811 20813; do if (exec 3<>/dev/tcp/127.0.0.1/$p) 2>/dev/null; then ok "порт $p слушает"; else bad "порт $p не слушает"; fi; done
if [ "${CP10_SKIP_INTERNET:-0}" != 1 ]; then
  for p in 20811 20813; do
    code=$(curl -s -o /dev/null -m 10 -w '%{http_code}' --socks5-hostname 127.0.0.1:$p ${CP10_PROBE_URL:-http://cp.cloudflare.com/generate_204})
    [ "$code" = 204 -o "$code" = 200 ] && ok "проба контроллера через вход $p: HTTP 204" || bad "проба контроллера через вход $p: HTTP '${code:-нет ответа}'"
  done
fi

echo "5. прочее"
[ -d /run ] && ok "/run доступен для сокета API" || bad "нет /run"
command -v python3 >/dev/null && command -v curl >/dev/null && ok "python3 и curl есть" || bad "нужны python3 и curl"
[ -f $CONF_DIR/config.json ] && echo "  --    $CONF_DIR/config.json уже есть (директор уже включён?)" || ok "директор ещё не настроен"
echo "  текущее значение commercial_router_probe_interval: $(hconfig_get commercial_router_probe_interval)"
echo
if [ $fail -eq 0 ]; then echo "ИТОГ: можно применять"; else echo "ИТОГ: НЕ применять, см. FAIL выше"; fi
exit $fail
