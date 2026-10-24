# STAND ONLY: build the qWDTT server from the audited source (clone at the commit read by Claude) with a checksum-verified Go toolchain
set -eu
GOV=go1.27.3; T=/opt/go-vkturn; mkdir -p $T
if [ ! -x $T/go/bin/go ]; then
  curl -sL -m 300 -o /tmp/$GOV.tgz https://go.dev/dl/$GOV.linux-amd64.tar.gz
  want=$(curl -s -m 20 "https://go.dev/dl/?mode=json&include=all" | python3 -c "import json,sys; d=json.load(sys.stdin); print([f['sha256'] for r in d if r['version']=='$GOV' for f in r['files'] if f['filename']=='$GOV.linux-amd64.tar.gz'][0])")
  have=$(sha256sum /tmp/$GOV.tgz | cut -d' ' -f1); echo "go toolchain sha256 published: ${want:0:16} downloaded: ${have:0:16}"; [ "$want" = "$have" ] || { echo "CHECKSUM MISMATCH"; exit 1; }
  tar -C $T -xzf /tmp/$GOV.tgz
fi
export PATH=$T/go/bin:$PATH GOFLAGS=-mod=mod GOTOOLCHAIN=local CGO_ENABLED=0 GOPATH=/tmp/gopath-vkturn
rm -rf /tmp/qwdtt-src; cp -r /work/stage2/vkturn/src/qwdtt /tmp/qwdtt-src; cd /tmp/qwdtt-src
go version; echo "-- build server"; go build -trimpath -ldflags "-s -w" -o /work/stage2/vkturn/wdtt-server ./server 2>&1 | tail -5
echo "-- build client (linux, for the local test only)"; go build -trimpath -ldflags "-s -w" -o /work/stage2/vkturn/wdtt-client ./go_client 2>&1 | tail -5
ls -la /work/stage2/vkturn/wdtt-server /work/stage2/vkturn/wdtt-client; sha256sum /work/stage2/vkturn/wdtt-server | cut -c1-16
