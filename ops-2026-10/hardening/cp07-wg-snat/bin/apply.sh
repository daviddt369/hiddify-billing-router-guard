#!/bin/bash
# CP-07 · применение на .190 (выполняет ВЛАДЕЛЕЦ от root: это правила iptables). Туннель не перезапускается, существующие соединения не рвутся.
# 1) правит две строки в wg-rude.conf (чтобы исправление пережило перезагрузку), 2) «на лету» подменяет правило NAT: сначала вставляет SNAT, потом удаляет MASQUERADE.
# Переменные (по умолчанию боевые): CONF, WANIF, PUB, CLIENT, WGIF.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
CONF=${CONF:-/etc/wireguard/wg-rude.conf}
WANIF=${WANIF:-ens3}
PUB=${PUB:-203.0.113.20}
CLIENT=${CLIENT:-10.66.66.2/32}
export CONF WANIF PUB CLIENT
BK=/root/.claude-backups/cp07-$(date +%Y%m%d-%H%M%S)

echo "== 0. проверка до"
bash "$HERE/check-before.sh"

if grep -q -- "-j SNAT --to-source $PUB" "$CONF"; then
  echo "== 1-2. файл уже содержит SNAT: резервная копия и правка не нужны (указатель на прежнюю копию не трогаем)"
else
  echo "== 1. резервная копия в $BK"
  install -d -m 700 "$BK"
  cp -a "$CONF" "$BK/$(basename "$CONF")"
  iptables-save -t nat > "$BK/nat.before" 2>/dev/null || true
  echo "$BK" > /run/cp07.last-backup

  echo "== 2. правка двух строк в $CONF (точная замена, владелец и права сохраняются)"
  python3 - "$CONF" "$WANIF" "$PUB" "$CLIENT" <<'PY'
import os, shutil, sys, tempfile
conf, wan, pub, client = sys.argv[1:5]
old_up = f"-t nat -C POSTROUTING -s {client} -o {wan} -j MASQUERADE 2>/dev/null || iptables -t nat -I POSTROUTING 1 -s {client} -o {wan} -j MASQUERADE"
new_up = f"-t nat -C POSTROUTING -s {client} -o {wan} -j SNAT --to-source {pub} 2>/dev/null || iptables -t nat -I POSTROUTING 1 -s {client} -o {wan} -j SNAT --to-source {pub}"
old_dn = f"-t nat -D POSTROUTING -s {client} -o {wan} -j MASQUERADE 2>/dev/null || true"
new_dn = f"-t nat -D POSTROUTING -s {client} -o {wan} -j SNAT --to-source {pub} 2>/dev/null || true"
text = open(conf, encoding="utf-8").read()
for old, new in ((old_up, new_up), (old_dn, new_dn)):
    if text.count(old) != 1:
        sys.exit(f"ОСТАНОВКА: строка не найдена ровно один раз: {old[:80]}…")
    text = text.replace(old, new)
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(conf), prefix=".cp07.")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.write(text)
shutil.copystat(conf, tmp)
st = os.stat(conf)
os.chown(tmp, st.st_uid, st.st_gid)
os.replace(tmp, conf)
print("  правка выполнена")
PY
fi

echo "== 3. правило «на лету»: сначала SNAT (позиция 1), потом удаление MASQUERADE — окна без NAT нет"
iptables -t nat -C POSTROUTING -s "$CLIENT" -o "$WANIF" -j SNAT --to-source "$PUB" 2>/dev/null \
  || iptables -t nat -I POSTROUTING 1 -s "$CLIENT" -o "$WANIF" -j SNAT --to-source "$PUB"
while iptables -t nat -C POSTROUTING -s "$CLIENT" -o "$WANIF" -j MASQUERADE 2>/dev/null; do
  iptables -t nat -D POSTROUTING -s "$CLIENT" -o "$WANIF" -j MASQUERADE
done
iptables -t nat -L POSTROUTING -n -v --line-numbers | grep -E "SNAT|MASQUERADE" | grep "${CLIENT%/32}" | sed 's/^/  /'

echo "== 4. проверка после"
bash "$HERE/check-after.sh" || true
echo "Откат: bash $HERE/rollback.sh $(cat /run/cp07.last-backup 2>/dev/null)"
