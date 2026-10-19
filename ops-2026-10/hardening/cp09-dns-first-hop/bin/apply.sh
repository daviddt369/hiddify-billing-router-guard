#!/bin/bash
# CP-09 v2 · DNS первого хопа: шифрованный DNS (DoH) НАПРЯМУЮ с узла вместо открытого UDP/53 (закрывает F-03). RU, от root.
# usage: apply.sh xray|singbox
# Меняет два файла движка: шаблон 02_dns.json.j2 (чтобы пережил следующие «Применить») и сгенерированный 02_dns.json, затем ОДИН раз перезапускает службу движка.
# До замены: проверка «до», временный экземпляр настоящего бинарника с НОВЫМ DNS открывает настоящий сайт. При любом сбое файлы и служба возвращаются автоматически.
set -uo pipefail
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"
setup_engine "${1:-}"
BK=/root/.claude-backups/cp09-$(date +%Y%m%d-%H%M%S)/$ENGINE

echo "== 0. проверка до ($ENGINE)"
bash "$HERE/check-before.sh" "$ENGINE" || { echo "проверка «до» не пройдена: ничего не изменено"; exit 2; }
if [ "$(sha16 "$J2")" = "$(sha16 "$T_AFTER")" ]; then echo "уже применено: шаблон новый. Ничего не делаю."; exit 0; fi

echo "== 1. новый сгенерированный файл"
DNS_SERVER=$(dns_server)
NEWGEN=$(mktemp "$CONFDIR/.cp09.XXXXXX")
render "$T_AFTER" "$DNS_SERVER" "$NEWGEN" || { rm -f "$NEWGEN"; echo "не удалось отрисовать новый файл"; exit 2; }
if [ "$ENGINE" = xray ]; then
  t=$(mktemp -d); cp "$CONFDIR"/*.json "$t"/; cp "$NEWGEN" "$t/02_dns.json"
  if ! "$BIN" run -test -confdir "$t" >/tmp/cp09-new.txt 2>&1; then echo "Xray отклонил новую конфигурацию: $(tail -1 /tmp/cp09-new.txt)"; rm -rf "$t" "$NEWGEN"; exit 2; fi
  rm -rf "$t"; echo "  xray run -test -confdir: Configuration OK"
fi

echo "== 2. временный экземпляр настоящего бинарника с НОВЫМ DNS (до замены файлов)"
if [ "${CP09_SKIP_PREFLIGHT:-0}" = 1 ]; then echo "  пропущено (CP09_SKIP_PREFLIGHT=1: лаборатория без интернета)"
else
  if ! preflight_instance "$NEWGEN"; then rm -f "$NEWGEN"; echo "новый DNS не работает на этом узле: ничего не изменено"; exit 2; fi
fi
chmod --reference="$GEN" "$NEWGEN"; chown --reference="$GEN" "$NEWGEN"
NEWJ2=$(mktemp "$CONFDIR/.cp09.XXXXXX"); cp "$T_AFTER" "$NEWJ2"; chmod --reference="$J2" "$NEWJ2"; chown --reference="$J2" "$NEWJ2"

echo "== 3. резервные копии в $BK"
install -d -m 700 "$(dirname "$BK")" "$BK"
cp -a "$J2" "$BK/02_dns.json.j2"; cp -a "$GEN" "$BK/02_dns.json"
echo "$BK" > "$PTR"
ss -ltnH 2>/dev/null | awk '{print $4}' | sort > "$BK/listen.before"        # какие порты слушаются до (после перезапуска набор должен вернуться)

revert() {
  echo "== ОТКАТ"; set +e
  cp -a "$BK/02_dns.json.j2" "$J2"; cp -a "$BK/02_dns.json" "$GEN"; eval "$RESTART_CMD"
  echo "  файлы и служба возвращены из $BK"
}

echo "== 4. замена файлов и ОДИН перезапуск $UNIT"
mv -f "$NEWJ2" "$J2"; mv -f "$NEWGEN" "$GEN"
if ! eval "$RESTART_CMD"; then revert; echo "перезапуск не удался"; exit 1; fi
sleep 4

echo "== 5. проверка после"
BK="$BK" bash "$HERE/check-after.sh" "$ENGINE"; rc=$?
if [ $rc -ne 0 ]; then revert; echo "ИТОГ: проверка «после» не пройдена, прежнее состояние возвращено"; exit 1; fi
echo "Откат вручную: bash $HERE/rollback.sh $ENGINE $BK"
