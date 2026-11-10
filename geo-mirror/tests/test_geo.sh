#!/bin/bash
# STAND: the geo-file mirror: the updater against a mock GitHub (good / bad releases, API down, digest lie, Telegram alert once a day) and the nginx deploy with its rollback.
set -u
G=/work/stage2/geo; P=$G/pkg; GOOD=/work/tests/fixtures/cheburnator/geo
export CGU_API_BASE=http://127.0.0.1:8098 CGU_DL_BASE=http://127.0.0.1:8098 TG_API_BASE=http://127.0.0.1:8098 CGU_RETRY_SLEEP=0
PASS=0; FAIL=0
t() { if eval "$2"; then echo "  OK    $1"; PASS=$((PASS+1)); else echo "  FAIL  $1"; FAIL=$((FAIL+1)); fi; }
rm -rf /tmp/mock /tmp/geo_t; mkdir -p /tmp/mock /tmp/geo_t
pkill -f mock_gh.py 2>/dev/null; python3 $G/mock_gh.py & MOCK=$!; sleep 1
mysql hiddifypanel -e "replace into str_config (child_id,\`key\`,value) values (0,'telegram_bot_token','TESTTOKEN'); update admin_user set telegram_id=777001 where mode='super_admin' order by id limit 1" 2>&1 | head -2
U="python3 $P/cheb_geo_update.py --repo o/n --profile $P/profile.json --www /tmp/geo_t/www --state /tmp/geo_t/state.json --last-good /tmp/geo_t/last-good"
sha() { sha256sum "$1" | cut -c1-64; }
rel() { python3 $G/make_variant.py $GOOD /tmp/mock/$1 $2 >/dev/null; }
tg() { [ -f /tmp/mock/tg.log ] && wc -l < /tmp/mock/tg.log || echo 0; }

echo "### 1 a good release is published"
rel v1 good; echo v1 > /tmp/mock/CURRENT
OUT=$($U 2>&1); echo "$OUT" | tail -2 | cut -c1-200
t "PUBLISHED v1, both files served, bytes equal the release" 'echo "$OUT" | grep -q "PUBLISHED v1" && [ "$(sha /tmp/geo_t/www/geoip.dat)" = "$(sha $GOOD/geoip.dat)" ] && [ "$(sha /tmp/geo_t/www/geosite.dat)" = "$(sha $GOOD/geosite.dat)" ]'
t "a copy is in last-good" '[ "$(sha /tmp/geo_t/last-good/geosite.dat)" = "$(sha $GOOD/geosite.dat)" ]'
S1="$(sha /tmp/geo_t/www/geosite.dat)$(sha /tmp/geo_t/www/geoip.dat)"

echo "### 2 the same tag again: nothing to do"
OUT=$($U 2>&1); t "already published" 'echo "$OUT" | grep -q "already published"'

echo "### 3 a release without a tag the profile uses (RU-DOMAINS)"
rel v2 missing_tag; echo v2 > /tmp/mock/CURRENT; N0=$(tg)
OUT=$($U 2>&1); echo "$OUT" | tail -2 | cut -c1-230
t "REJECTED, the served files did not change" 'echo "$OUT" | grep -q "REJECTED v2" && [ "$S1" = "$(sha /tmp/geo_t/www/geosite.dat)$(sha /tmp/geo_t/www/geoip.dat)" ]'
t "the super admin got ONE message (to the admin chat, not a client)" '[ "$(tg)" = "$((N0+1))" ] && tail -1 /tmp/mock/tg.log | grep -q "chat=777001"'
OUT=$($U 2>&1); t "the same rejection again: no second message the same day" '[ "$(tg)" = "$((N0+1))" ]'

echo "### 4 a truncated file / a lying digest / a file that lost two thirds of its entries / an html page instead of a file"
rel v3 truncated; echo v3 > /tmp/mock/CURRENT; OUT=$($U 2>&1); echo "$OUT" | tail -1 | cut -c1-200
t "truncated geosite: REJECTED (odd size)" 'echo "$OUT" | grep -q "REJECTED v3.*odd size"'
rel v4 good; echo '{"geoip.dat": "sha256:0000000000000000000000000000000000000000000000000000000000000000"}' > /tmp/mock/v4/digest_override.json; echo v4 > /tmp/mock/CURRENT; OUT=$($U 2>&1); echo "$OUT" | tail -1 | cut -c1-200
t "digest of GitHub differs from the downloaded bytes: REJECTED" 'echo "$OUT" | grep -q "REJECTED v4.*sha256"'
rel v5 halved; echo v5 > /tmp/mock/CURRENT; OUT=$($U 2>&1); echo "$OUT" | tail -1 | cut -c1-200
t "RU-IPS shrank to a third: REJECTED" 'echo "$OUT" | grep -q "REJECTED v5.*shrank"'
rel v6 badmagic; echo v6 > /tmp/mock/CURRENT; OUT=$($U 2>&1); echo "$OUT" | tail -1 | cut -c1-200
t "an html page instead of geoip.dat: REJECTED" 'echo "$OUT" | grep -q "REJECTED v6"'
t "after all rejections the served files are still v1" '[ "$S1" = "$(sha /tmp/geo_t/www/geosite.dat)$(sha /tmp/geo_t/www/geoip.dat)" ] && grep -q "\"tag\": \"v1\"" /tmp/geo_t/state.json'

echo "### 5 the API is down"
echo DOWN > /tmp/mock/CURRENT; N1=$(tg); OUT=$($U 2>&1); echo "$OUT" | tail -1 | cut -c1-200
t "not reachable: nothing changed, no message (the files are fresh)" 'echo "$OUT" | grep -q "not reachable" && [ "$S1" = "$(sha /tmp/geo_t/www/geosite.dat)$(sha /tmp/geo_t/www/geoip.dat)" ] && [ "$(tg)" = "$N1" ]'

echo "### 6 the next good release replaces the files"
rel v7 good; echo v7 > /tmp/mock/CURRENT; OUT=$($U 2>&1); echo "$OUT" | tail -1 | cut -c1-200
t "PUBLISHED v7, the old error is cleared" 'echo "$OUT" | grep -q "PUBLISHED v7" && ! grep -q last_error /tmp/geo_t/state.json'

echo "### 7 the real xray is the last gate (a rule that passes our own tag check but that xray cannot build: a broken regexp)"
python3 - <<'PY'
import json
p = json.load(open("/work/stage2/geo/pkg/profile.json"))
p["DirectSites"] = p["DirectSites"] + ["regexp:(["]
json.dump(p, open("/tmp/geo_t/profile_attr.json", "w"))
PY
OUT=$(python3 $P/cheb_geo_update.py --repo o/n --profile /tmp/geo_t/profile_attr.json --www /tmp/geo_t/www2 --state /tmp/geo_t/state2.json --last-good /tmp/geo_t/lg2 2>&1); echo "$OUT" | tail -1 | cut -c1-260
t "a rule that real xray cannot load is caught before publishing" 'echo "$OUT" | grep -q "REJECTED" && [ ! -e /tmp/geo_t/www2/geoip.dat ]'
mysql hiddifypanel -e "delete from str_config where \`key\`='telegram_bot_token'; update admin_user set telegram_id=NULL where mode='super_admin'" 2>&1 | head -2
kill $MOCK 2>/dev/null
echo; echo "updater tests: $PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
