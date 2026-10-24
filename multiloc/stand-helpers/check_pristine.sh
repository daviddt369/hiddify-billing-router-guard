#!/bin/bash
# compares live files with the sha256 of the PRISTINE prod copy recorded at the beginning of the work
PKG=/opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel
declare -A W=(
 [$PKG/drivers/xray_api.py]=d4cd23bf8bedc9931762f87b3e9fdc38fbdacee5fe44a499d78c83e2ffa62794
 [$PKG/hutils/commercial_routing.py]=373f100abb680832ed352845bcf5659585adbdcd7908a6dba8bac0752a1a1fae
 [$PKG/hutils/proxy/clash.py]=d6513c5077197be9e095c54f1a082665d885ac539c75e38e7abf32a2e2a7cff8
 [$PKG/hutils/proxy/shared.py]=298f0b5eb3956fbd72ac2fc776d44da7f3ed21adcc0a1175d45353170896b049
 [$PKG/hutils/proxy/singbox.py]=7f4cbabd55c9d57e4a80964145977db3e98d2148df8a7fa6e5de2f216d38dd7c
 [$PKG/panel/hiddify.py]=8aac1cdcd4365c6b2d315f2f1b2699ca8985d4b54892943ba0dc95f3e88b26ef
 [$PKG/panel/user/templates/clash_config.yml]=3d4dfbbfda9440518466e86e4f82bf0e5439968a97c5a8f292300d72faca8e2b
 [$PKG/panel/admin/__init__.py]=d79ae1e85f4202318372ac060c462d7c77bef0aa92ffb1b4afe7c457f5d60cb4
 [$PKG/hutils/proxy/xrayjson.py]=022e238b5bb5a8d2e69cce3b19f6b0ec93b0ccf13c3ca6c4ba2dd5f9e56304d6
 [$PKG/panel/user/user.py]=034cdbf6b2cb15b2387be752bb0e6958eb4d6dcee608aff685ca4fbe2123d854
 [$PKG/panel/commercial/telegrambot/Usage.py]=fad3f6f97630ee7d68710c998aa0dce80f78fcd850eabe45631ccd094f6140bf
 [$PKG/panel/admin/templates/routing-upstream.html]=6da033c60a16a1a4d14a775893a9ffecf29d8d97c827accfeedc514a634e67df
 [$PKG/panel/commercial/restapi/v2/user/configs_api.py]=2409508ca67b060eb84a373103156c45b7064a044ec82637be4d3adbaa1cc2dc
 [$PKG/panel/admin/RoutingRuleSourceAdmin.py]=48b816c2001a2c894f707f937350889f57b2ddef5346c735603d073b13fe7533
 [$PKG/panel/admin/BusinessAdmin.py]=46b76d795188c68e3c2351d778db73311ca2ad0919d2374d1d1307d9c5189834
 [$PKG/panel/admin/templates/routing-rule-source.html]=5c4f1bb25ec454d3801945297a14cc19d7b8f94c65ed0f41e995508cbec722d0
 [/opt/hiddify-manager/common/commander.py]=704e71394b2826fe3db8ee420287bcf6df8521f85f366f249b8ad28c2091927b
 [/opt/hiddify-manager/haproxy/fronts/in_tcpmode.cfg.pj2]=0bffef11d33179db470a5777cf9e91d3d917476155fde8f7c00048d6ebdb8b41
 [/opt/hiddify-manager/haproxy/haproxy.cfg.j2]=796f88536ee779d195fa86dfc83abcbf38a2014c6a9bbbfbef2606b027ffc5a5
 [/opt/hiddify-manager/haproxy/maps/path_v10.j2]=8b103375e5285c8385d43a7f82387d02c916b1d80a17fe534147d1cf98988ced
 [/opt/hiddify-manager/xray/configs/03_routing.json.j2]=98d6254f6de569749f63bd693f913e98f43d813b540051dc188cbca2d62708e9
 [/opt/hiddify-manager/xray/configs/06_outbounds.json.j2]=5fe8f6968f68798811ce82ee099dfa96ae0da31130288934830e74ccb65ea260
)
bad=0
PREFIX="${PREFIX:-}"
for f in "${!W[@]}"; do
  cur=$(sha256sum "$PREFIX$f" 2>/dev/null | cut -d' ' -f1)
  if [ "$cur" = "${W[$f]}" ]; then echo "PRISTINE  ${f##*/}"; else echo "MODIFIED  $f"; bad=1; fi
done
exit $bad
