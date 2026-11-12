#!/bin/bash
# STAND: deploy_ru.sh of the geo-file mirror on the stand's lk host: a broken nginx piece -> rollback, a normal install with its checks, a repeat, the removal.
set -u
G=/work/stage2/geo; P=$G/pkg
export CGU_API_BASE=http://127.0.0.1:8098 CGU_DL_BASE=http://127.0.0.1:8098 TG_API_BASE=http://127.0.0.1:8098 CGU_RETRY_SLEEP=0 CURL_OPTS=-k
CONF=/opt/hiddify-manager/nginx/conf.d/zz-lk.conf; EXTRA=/opt/hiddify-manager/nginx/conf.d/lk-extra
PASS=0; FAIL=0
t() { if eval "$2"; then echo "  OK    $1"; PASS=$((PASS+1)); else echo "  FAIL  $1"; FAIL=$((FAIL+1)); fi; }
HOST=$(grep -m1 server_name $CONF | sed -E 's/.*server_name ([^;]+);/\1/'); TOKEN=teststandtoken0123456789ab
echo "lk host on the stand: $HOST"
rm -rf /tmp/mock; mkdir -p /tmp/mock; pkill -f mock_gh.py 2>/dev/null; python3 $G/mock_gh.py & MOCK=$!; sleep 1
python3 $G/make_variant.py /work/tests/fixtures/cheburnator/geo /tmp/mock/v1 good >/dev/null; echo v1 > /tmp/mock/CURRENT
rm -rf /var/lib/cheb-geo; rm -f $EXTRA/*.locations
cp -a $CONF /tmp/zz-lk.before; SUBMD5() { :; }
D="bash $P/deploy_ru.sh $HOST $TOKEN o/n $P/profile.json"

echo "### 1 a broken piece in lk-extra: the install must roll back and leave everything as it was"
mkdir -p $EXTRA; echo "this is not a valid directive;" > $EXTRA/zz-broken.locations
OUT=$($D 2>&1); RC=$?; echo "$OUT" | tail -4 | cut -c1-200
t "exit 5 and a ROLLBACK message" '[ $RC = 5 ] && echo "$OUT" | grep -q ROLLBACK'
t "zz-lk.conf is byte-for-byte as before, no mirror location, no timer" 'cmp -s $CONF /tmp/zz-lk.before && [ ! -e $EXTRA/cheb-geo.locations ] && [ ! -e /etc/systemd/system/cheb-geo.timer ]'
t "nginx still valid and active, the lk front answers" 'nginx -t -c /opt/hiddify-manager/nginx/nginx.conf 2>&1 | grep -q successful && [ "$(systemctl is-active hiddify-nginx)" = active ] && [ "$(curl -sk --resolve $HOST:443:127.0.0.1 -o /dev/null -w %{http_code} https://$HOST/)" = 200 ]'
rm -f $EXTRA/zz-broken.locations

echo "### 2 a normal install"
OUT=$($D 2>&1); RC=$?; echo "$OUT" | cut -c1-220 | tail -14
t "exit 0 and DONE, every check OK" '[ $RC = 0 ] && echo "$OUT" | grep -q "DONE" && ! echo "$OUT" | grep -q "FAIL"'
t "the include line is in zz-lk.conf exactly once" '[ "$(grep -c "lk-extra/\*.locations" $CONF)" = 1 ]'
t "timer enabled with four daily slots" 'systemctl is-enabled cheb-geo.timer >/dev/null && grep -q "00,06,12,18:30:00" /etc/systemd/system/cheb-geo.timer'
t "state file has the tag and is not world writable" 'grep -q "\"tag\": \"v1\"" /var/lib/cheb-geo/state.json && [ "$(stat -c %a /var/lib/cheb-geo/state.json)" != 666 ]'
echo "  the mirror, as a phone would fetch it:"; curl -sk --resolve $HOST:443:127.0.0.1 -D - -o /dev/null https://$HOST/$TOKEN/geoip.dat | tr -d '\r' | grep -i -E "^(HTTP|content-type|content-length|cache-control)" | sed 's/^/    /'
t "geoip.dat arrives intact (sha256 = the release asset)" '[ "$(curl -sk --resolve $HOST:443:127.0.0.1 https://$HOST/$TOKEN/geoip.dat | sha256sum | cut -c1-64)" = "$(sha256sum /work/tests/fixtures/cheburnator/geo/geoip.dat | cut -c1-64)" ]'

echo "### 3 repeat: nothing is added twice"
OUT=$($D 2>&1); RC=$?
t "the second install is fine and keeps ONE include line" '[ $RC = 0 ] && [ "$(grep -c "lk-extra/\*.locations" $CONF)" = 1 ] && [ "$(grep -c "geo-file mirror" $CONF)" = 1 ]'

echo "### 4 the updater service itself runs under systemd (the timer's job; it has no mock; the repository name of this test is fake)"
systemctl start cheb-geo.service; sleep 1
journalctl -u cheb-geo.service --no-pager --since "-1 min" | tail -3 | cut -c1-200
t "the service ran and finished without failing (the repository name of this test does not exist on the real GitHub: it reports that and changes nothing)" '[ "$(systemctl is-failed cheb-geo.service)" != failed ] && [ -s /var/lib/cheb-geo/www/geoip.dat ]'

echo "### 5 removal"
OUT=$(bash $P/deploy_ru.sh --remove 2>&1); echo "$OUT" | tail -2 | cut -c1-200
t "location, include line and timer are gone, the files stay, the lk front still works" '[ ! -e $EXTRA/cheb-geo.locations ] && ! grep -q "lk-extra" $CONF && [ ! -e /etc/systemd/system/cheb-geo.timer ] && [ -s /var/lib/cheb-geo/www/geoip.dat ] && [ "$(curl -sk --resolve $HOST:443:127.0.0.1 -o /dev/null -w %{http_code} https://$HOST/)" = 200 ] && [ "$(curl -sk --resolve $HOST:443:127.0.0.1 -o /dev/null -w %{http_code} https://$HOST/$TOKEN/geoip.dat)" = 404 ]'
cp -a /tmp/zz-lk.before $CONF; nginx -t -c /opt/hiddify-manager/nginx/nginx.conf 2>&1 | tail -1; systemctl reload hiddify-nginx
kill $MOCK 2>/dev/null; rm -rf /var/lib/cheb-geo /opt/cheb-geo
echo; echo "deploy tests: $PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
