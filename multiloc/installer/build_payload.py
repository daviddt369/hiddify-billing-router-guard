#!/usr/bin/env python3
"""Generates the MODIFIED payload files from the pristine prod-identical files in ./base (exact, asserted replacements).
Re-run after changing a patch. NEW files (multiloc.py, ...) are edited directly in ./payload and are not touched here.

  python3 build_payload.py        -> writes ./payload/<abs path> for every modified file, prints a one-line summary per file
"""
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(HERE, "base")
OUT = os.path.join(HERE, "payload")
PKG = "opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel"

PATCHES = {}   # relpath -> list of (old, new)  (each `old` must occur exactly once) ; or ("APPEND", text)


def patch(rel, *pairs):
    PATCHES.setdefault(rel, []).extend(pairs)


# ------------------------------------------------------------------------------------------------ python: panel code
patch(f"{PKG}/hutils/commercial_routing.py", ("APPEND", '''

# BEGIN HIDDIFY MULTILOC ROUTER LAYER
# Multi-location (country entrances): LAST layer of the apply pipeline. Every earlier layer and `_is_final_global_rule` are
# untouched. If the router config already reflects the plan (or the multi-location tables do not exist) this layer changes nothing and
# does not restart xray-router; otherwise it restarts it once and waits until the entrances really listen (restore + restart if not).

try:
    _HIDDIFY_MULTILOC_PREVIOUS_APPLY_ROUTER_CORE_CONFIG
except NameError:
    _HIDDIFY_MULTILOC_PREVIOUS_APPLY_ROUTER_CORE_CONFIG = apply_router_core_config

    def apply_router_core_config(*args, **kwargs):
        result = _HIDDIFY_MULTILOC_PREVIOUS_APPLY_ROUTER_CORE_CONFIG(*args, **kwargs)
        from hiddifypanel.hutils import multiloc_router
        info = multiloc_router.patch_config(COMMERCIAL_ROUTING_RUNTIME_CONFIG_PATH)
        print("Applied multi-location router layer: " + str(info))
        return result

# END HIDDIFY MULTILOC ROUTER LAYER
'''))

patch(f"{PKG}/panel/hiddify.py", (
    """        configs['panel_links'].append(get_account_panel_link(owner, d.domain))
    

    return configs""",
    """        configs['panel_links'].append(get_account_panel_link(owner, d.domain))
    

    # multi-location: slot data for the xray / haproxy templates (empty list when the feature is not installed)
    try:
        from hiddifypanel.hutils import multiloc
        configs['locations'] = multiloc.template_context()
    except Exception:
        configs['locations'] = []

    return configs"""))

patch(f"{PKG}/hutils/proxy/shared.py", (
    """                if 'msg' not in pinfo:
                    allp.append(pinfo)
    return allp
""",
    """                if 'msg' not in pinfo:
                    allp.append(pinfo)
    # multi-location hook: adds the AUTO / country profiles for users allowed to see them (returns `allp` untouched otherwise)
    try:
        from hiddifypanel.hutils import multiloc
        return multiloc.append_location_entries(allp)
    except Exception:
        return allp
"""))

patch(f"{PKG}/drivers/xray_api.py", (
    """    # @cache.cache(ttl=300)
    def get_inbound_tags(self):
        try:
            xray_client = self.get_xray_client()
            inbounds = {inb.name.split(">>>")[1] for inb in xray_client.stats_query('inbound')}
            # print(f"Success in get inbound tags {inbounds}")
        except Exception as e:
            print(f"error in get inbound tags {e}")
            inbounds = {}
        return list(inbounds)
""",
    """    # @cache.cache(ttl=300)
    def get_inbound_tags(self):
        # tags come from BOTH the traffic stats and the running xray's own inbound list (`xray api lsi`): a user must be added to
        # / removed from every inbound even when the stats of an inbound are missing (stats disabled, fresh inbound, ...).
        inbounds = set()
        try:
            xray_client = self.get_xray_client()
            inbounds = {inb.name.split(">>>")[1] for inb in xray_client.stats_query('inbound')}
            # print(f"Success in get inbound tags {inbounds}")
        except Exception as e:
            print(f"error in get inbound tags {e}")
            inbounds = set()
        inbounds |= self._lsi_inbound_tags()
        # deterministic order, legacy VLESS inbounds first: get_enabled_users_terminal() uses the FIRST inbound that answers as its
        # reference list of "users present in xray", so the reference must not jump between inbounds from one run to the next
        return sorted(inbounds, key=lambda t: (0 if (t.startswith('v10-vless') and '-loc' not in t) else 1, t))

    def _lsi_inbound_tags(self):
        import json
        import subprocess
        try:
            res = subprocess.run(['xray', 'api', 'lsi', '--server=127.0.0.1:10085'], capture_output=True, text=True, timeout=8)
            if res.returncode != 0:
                return set()
            return {i['tag'] for i in json.loads(res.stdout).get('inbounds', []) if isinstance(i, dict) and i.get('tag')}
        except Exception as e:
            print(f"error in lsi inbound tags {e}")
            return set()
"""))

patch(f"{PKG}/drivers/xray_api.py",
      (
          """        xray_client.add_client(t, f'{uuid}', f'{uuid}@hiddify.com', protocol=protocol, flow=flow, alter_id=0, cipher='chacha20_poly1305')

    def add_client(self, user):""",
          """        if protocol == 'trojan':
            # xtlsapi 3.3.0 builds the Trojan Account with a `flow` field that does not exist -> ValueError, swallowed by add_client():
            # Trojan users were never hot-added. Build the account without `flow` and call the gRPC handler directly.
            self._add_trojan_account(xray_client, t, uuid)
            return
        xray_client.add_client(t, f'{uuid}', f'{uuid}@hiddify.com', protocol=protocol, flow=flow, alter_id=0, cipher='chacha20_poly1305')

    def _add_trojan_account(self, xray_client, tag, uuid):
        from xtlsapi import exceptions as xexc
        from xtlsapi.xray_api.common.protocol import user_pb2
        from xtlsapi.xray_api.app.proxyman.command import command_pb2
        from xtlsapi.xray_api.proxy.trojan import config_pb2
        from xtlsapi.ext import utils
        from grpc._channel import _InactiveRpcError
        email = f'{uuid}@hiddify.com'
        try:
            xray_client.handler_stub.AlterInbound(command_pb2.AlterInboundRequest(
                tag=tag,
                operation=utils.to_typed_message(command_pb2.AddUserOperation(user=user_pb2.User(
                    email=email, level=0, account=utils.to_typed_message(config_pb2.Account(password=f'{uuid}')))))))
        except _InactiveRpcError as e:
            details = e.details()
            if details.endswith(f"User {email} already exists."):
                raise xexc.EmailAlreadyExists(details, email)
            raise xexc.XRayException(details)

    def add_client(self, user):"""))

# ------------------------------------------------------------------------------------------------ full xray JSON: one group button per location
patch(f"{PKG}/hutils/proxy/xrayjson.py",
      ("""        outbounds = []
        for proxy in hutils.proxy.get_valid_proxies(domains):""",
       """        outbounds = []
        proxy_outbounds = []   # (proxy, outbound) pairs for the multi-location group layout
        for proxy in hutils.proxy.get_valid_proxies(domains):"""),
      ("""            outbound = to_xray(proxy)
            if len(outbound):
                outbounds.append(outbound)
""",
       """            outbound = to_xray(proxy)
            if len(outbound):
                outbounds.append(outbound)
                proxy_outbounds.append((proxy, outbound))
"""),
      ("""            'base_xray_config.json.j2', remarks=remarks))
        if len(outbounds) > 1:
""",
       """            'base_xray_config.json.j2', remarks=remarks))
        group_layout = None
        try:  # multi-location: ONLY the dedicated /happ/ link shows one button per location (AUTO / countries)
            from hiddifypanel.hutils import multiloc
            group_layout = multiloc.xray_json_layout(proxy_outbounds, base_config, str(user.uuid))
        except Exception:
            group_layout = None
        if group_layout is None:  # the stock list: a location entry must never end up in it (hidden AUTO, an error, any other fallback)
            outbounds = [o for p, o in proxy_outbounds if not p.get('multiloc')]
        if group_layout is not None:
            all_configs = list(group_layout)  # Happ / INCY read the usage from the subscription headers: no extra "usage" button
        elif len(outbounds) > 1:
"""))

patch(f"{PKG}/panel/user/user.py", (
    """        configs = hutils.proxy.xrayjson.configs_as_json(c['domains'], c['user'], c['expire_days'], c['profile_title'])
        return add_headers(configs, c, 'application/json')
""",
    """        configs = hutils.proxy.xrayjson.configs_as_json(c['domains'], c['user'], c['expire_days'], c['profile_title'])
        return add_headers(configs, c, 'application/json')

    # multi-location: the dedicated link for Happ / INCY (AUTO + countries as three buttons). The usual link and every other endpoint are untouched.
    @route("/happ/")
    @route("/happ")
    @login_required(roles={Role.user})
    def happ_locations(self):
        '''Returns the Happ / INCY full Xray JSON: one group button per location (the stock list for accounts that are not shown AUTO)'''
        resp = self.xray()
        try:  # Happ / INCY open this address from their "Site" button: it must be the personal cabinet, not this JSON
            resp.headers['profile-web-page-url'] = re.sub(r"happ/?$", "", resp.headers.get('profile-web-page-url', ''))
        except Exception:
            pass
        try:  # RoscomVPN for the JSON profiles: the profile (geo files) travels with the subscription, its rules are already inside the JSON
            from hiddifypanel.hutils import multiloc
            roscom_header = multiloc.roscom_header_used()
            if roscom_header:
                resp.headers['routing'] = roscom_header
        except Exception:
            pass
        return resp
"""))

# ------------------------------------------------------------------------------------------------ personal cabinet "All Configs": the Happ / INCY entry right after "Auto"
patch(f"{PKG}/panel/commercial/restapi/v2/user/configs_api.py", (
    """                f"{base_url}auto/?asn={c['asn']}#{config_name}"
            )
        )
""",
    """                f"{base_url}auto/?asn={c['asn']}#{config_name}"
            )
        )

        # multi-location: the dedicated Happ / INCY link (AUTO + countries) for accounts that are shown the AUTO block - right after "Auto"
        try:
            from hiddifypanel.hutils import multiloc
            happ_link = multiloc.happ_link_for(g.account, f"{base_url}#{config_name}")
            if happ_link:
                items.append(create_item("Happ (AUTO + countries)", "ALL", "", "", "", "", happ_link))
        except Exception:
            pass
"""))

# ------------------------------------------------------------------------------------------------ Telegram bot: second link (Happ / INCY, three buttons)
patch(f"{PKG}/panel/commercial/telegrambot/Usage.py",
      ("""def _subscription_links_message(user: User) -> str:
    domain = Domain.get_domains()[0]
    user_link = hiddify.get_account_panel_link(user, domain.domain)
    return _(
""",
       """def _multiloc_happ_link(user: User, user_link: str):
    \"\"\"multi-location: second link (Happ / INCY, three buttons) for accounts that are shown the AUTO block; None otherwise\"\"\"
    try:
        from hiddifypanel.hutils import multiloc
        return multiloc.happ_link_for(user, user_link)
    except Exception:
        return None


def _subscription_links_message(user: User) -> str:
    domain = Domain.get_domains()[0]
    user_link = hiddify.get_account_panel_link(user, domain.domain)
    happ_link = _multiloc_happ_link(user, user_link)
    if happ_link:
        return _(
            "Вот ваши ссылки на подписку:\\n\\n"
            "Hiddify и другие приложения:\\n"
            "%(user_link)s\\n\\n"
            "Happ и INCY (три кнопки: AUTO и страны):\\n"
            "%(happ_link)s\\n\\n"
            "Скопируйте нужную ссылку и вставьте в ваш клиент. Первая ссылка открывает личный кабинет.",
            user_link=user_link,
            happ_link=happ_link,
        )
    return _(
"""),
      ("""def _subscription_link_keyboard(user: User):
    domain = Domain.get_domains()[0]
    user_link = hiddify.get_account_panel_link(user, domain.domain)
    return types.InlineKeyboardMarkup(
        keyboard=[
            [
                types.InlineKeyboardButton(
                    text=_("Открыть подписку"),
                    url=user_link,
                )
            ]
        ]
    )
""",
       """def _subscription_link_keyboard(user: User):
    domain = Domain.get_domains()[0]
    user_link = hiddify.get_account_panel_link(user, domain.domain)
    rows = [
        [
            types.InlineKeyboardButton(
                text=_("Открыть подписку"),
                url=user_link,
            )
        ]
    ]
    happ_link = _multiloc_happ_link(user, user_link)
    if happ_link:
        rows.append([types.InlineKeyboardButton(text=_("Happ / INCY: три кнопки"), url=happ_link)])
    return types.InlineKeyboardMarkup(keyboard=rows)
"""))

# ------------------------------------------------------------------------------------------------ admin UI (minimal)
patch(f"{PKG}/panel/admin/__init__.py", (
    """    # ROUTING_UPSTREAM_ADMIN_END --- do not remove this line
""",
    """    # ROUTING_UPSTREAM_ADMIN_END --- do not remove this line
    # MULTILOC_ADMIN_BEGIN --- one more RoutingAdmin layer: the "Страны / локации" page (silently skipped on any error)
    multiloc_layer_loaded = False
    if routing_enabled and RoutingAdmin is not None:
        try:
            from .RoutingLocationAdmin import make_routing_admin as _multiloc_make_routing_admin
            RoutingAdmin = _multiloc_make_routing_admin(RoutingAdmin)
            multiloc_layer_loaded = True
        except Exception:
            logger.exception("RoutingLocationAdmin unavailable; locations UI disabled")
    # MULTILOC_ADMIN_END --- do not remove this line
"""),
    (
        """    routing_admin_view_enabled = False
""",
        """    routing_admin_view_enabled = False
    multiloc_locations_menu_enabled = False   # MULTILOC: the sidebar entry "Страны / локации" (true once the locations layer is registered)
"""),
    (
        """            "routing_admin_view_enabled": routing_admin_view_enabled,
""",
        """            "routing_admin_view_enabled": routing_admin_view_enabled,
            "multiloc_locations_menu_enabled": multiloc_locations_menu_enabled,
"""),
    (
        """        RoutingAdmin.register(admin_bp)
        routing_admin_view_enabled = True
""",
        """        RoutingAdmin.register(admin_bp)
        routing_admin_view_enabled = True
        multiloc_locations_menu_enabled = multiloc_layer_loaded
"""))

patch(f"{PKG}/templates/admin-layout.html", (
    """          {{ render_nav_item('admin.RoutingAdmin:rule_source_list',
          icon('solid','list','nav-icon')+'Источники правил',_use_li=True) }}
          <!-- ROUTING_SIDEBAR_END -->
""",
    """          {{ render_nav_item('admin.RoutingAdmin:rule_source_list',
          icon('solid','list','nav-icon')+'Источники правил',_use_li=True) }}
          {% if multiloc_locations_menu_enabled %}
          {{ render_nav_item('admin.RoutingAdmin:location_list',
          icon('solid','globe','nav-icon')+'Страны / локации',_use_li=True) }}
          {% endif %}
          <!-- ROUTING_SIDEBAR_END -->
"""))
# ------------------------------------------------------------------------------------------------ router apply in the BACKGROUND (panel buttons)
# The panel waited 60 s (rule sources) / 90 s (routing "Save") for the whole legacy pipeline and KILLED it at the timeout: on a production-sized router the
# first restart was done, the later layers and the country entrances were never applied. Now every such button starts the apply as a systemd service
# (scripts/multiloc_router_apply.py) through ONE new subcommand of the panel's existing root entry point commander.py, and returns at once.
patch("opt/hiddify-manager/common/commander.py", (
    """# ROUTING_INSTALL_END --- do not remove this line

if __name__ == "__main__":
    cli()""",
    """# ROUTING_INSTALL_END --- do not remove this line


# MULTILOC_ROUTER_APPLY_BEGIN --- the panel's apply buttons start the router apply in the BACKGROUND (scripts/multiloc_router_apply.py); do not remove this line
@cli.command('router-apply-start')
@click.option('--mode', type=click.Choice(['auto', 'countries', 'full', 'accept', 'discard']), required=True)
def router_apply_start(mode):
    # fixed command, fixed script, a whitelisted mode: prints one JSON line; exit 0 = started, 3 = another apply is running, 2 = could not start
    raise SystemExit(subprocess.run([os.path.join(HIDDIFY_DIR, '.venv313/bin/python'), os.path.join(HIDDIFY_DIR, 'scripts/multiloc_router_apply.py'),
                                     'start', '--mode', mode]).returncode)
# MULTILOC_ROUTER_APPLY_END --- do not remove this line

if __name__ == "__main__":
    cli()"""))

patch(f"{PKG}/panel/admin/RoutingRuleSourceAdmin.py", (
    "REGION",
    '    @route("/rule-sources/apply-router/", methods=["POST"])\n',
    "    # ── Import ",
    '''    @route("/rule-sources/apply-router/", methods=["POST"])
    def apply_router(self):
        # the single "Apply" of the routing area (the same action behind the button of every page): it decides by itself what is needed - nothing / the countries /
        # the full cycle - and runs it in the background (waiting 60 s and then killing the pipeline dropped the country entrances)
        from hiddifypanel.hutils import multiloc_apply
        pl, res = multiloc_apply.apply_now()
        text, category = multiloc_apply.flash_for(res, pl)
        flash(text, category)
        return redirect(_sources_url())

''',
    ['timeout=60', 'commercial-routing-apply', 'def apply_router(self):']))

patch(f"{PKG}/panel/admin/BusinessAdmin.py", (
    "REGION",
    "            try:\n                import subprocess\n                apply_proc = subprocess.run(",
    "        else:\n            db.session.commit()",
    '''            try:
                # the routing settings are saved above; "Apply" = the single Apply of the routing area (nothing / the countries / the full cycle, in the background;
                # waiting 90 s and then killing the pipeline dropped the country entrances)
                from hiddifypanel.hutils import multiloc_apply
                apply_plan, apply_result = multiloc_apply.apply_now()
                commercial_routing_notice, notice_category = multiloc_apply.flash_for(apply_result, apply_plan, saved=True)
                hutils.flask.flash(commercial_routing_notice, notice_category)
            except Exception as exc:
                commercial_routing_notice = "Настройки сохранены, но router-core config не применён: " + _format_routing_apply_error(str(exc))
                hutils.flask.flash(commercial_routing_notice, "danger")
''',
    ['timeout=90', 'commercial-routing-apply', '_format_routing_apply_error(str(exc))']))

patch(f"{PKG}/panel/admin/templates/routing-rule-source.html",
      ("""    <form action="{{ sources_url }}apply-router/" method="POST"
          onsubmit="return confirm('Применить конфиг xray-router с текущими правилами?')">
      <button type="submit" class="btn btn-success btn-sm">▶ Применить xray-router</button>
    </form>
  </div>
</div>
""",
       """  </div>
</div>
{% include 'routing-apply-bar.html' %}
"""),
      ("""  После любого изменения нажмите <strong>▶ Применить xray-router</strong>.<br>""",
       """  Источники и правила сохраняются сразу; на router они попадают после кнопки <strong>«Применить»</strong> выше (она сама решает, нужен ли перезапуск).<br>"""))

patch(f"{PKG}/panel/admin/templates/routing-upstream.html",
      ("""            Нода применяется только после нажатия <strong>«Применить»</strong> в разделе «Маршрутизация».
          </div>
""",
       """            Ноды сохраняются сразу; на router они попадают после кнопки <strong>«Применить»</strong> ниже (она сама решает, нужен ли перезапуск).
          </div>
          {% include 'routing-apply-bar.html' %}
"""))

patch(f"{PKG}/templates/business-settings.html",
      ("""          {% if commercial_routing_notice %}
          <div class="alert alert-warning">{{ commercial_routing_notice }}</div>
          {% endif %}
""",
       """          {% if commercial_routing_notice %}
          <div class="alert alert-warning">{{ commercial_routing_notice }}</div>
          {% endif %}
          {% if active_section == "routing" %}{% set show_apply_button = False %}{% include 'routing-apply-bar.html' %}{% endif %}
"""),
      ("""          <form method="post" action="{{ request.path }}">
""",
       """          <form method="post" action="{{ request.path }}"{% if active_section == "routing" %} onsubmit="return confirm('Применить настройки маршрутизации?\\n\\nНастройки сохранятся. Если правила, ноды или настройки изменились: конфиг готовится в фоне (на проде около 15–20 минут, router работает как раньше), затем ОДИН перезапуск router (на проде ≈ 4–8 минут без соединений через РФ-ноду). Если изменений нет — перезапуска не будет. Идёт в фоне.')"{% endif %}>
"""),
      ("""            <button type="submit" class="btn btn-primary">{{ form.submit.label.text }}</button>""",
       """            <button type="submit" class="btn btn-primary">{% if active_section == "routing" %}Применить{% else %}{{ form.submit.label.text }}{% endif %}</button>"""))

# ------------------------------------------------------------------------------------------------ xray templates
patch("opt/hiddify-manager/xray/configs/03_routing.json.j2",
      (
          """         {
                "type":"field",
                "outboundTag": {% if hconfigs['commercial_routing_enable'] and hconfigs['commercial_apply_to_xray'] and hconfigs.get('commercial_legacy_geosite_to_router', True) %}"commercial-local-router"{% elif hconfigs['warp_mode'] == 'disable' %}"freedom"{% else %}"WARP"{% endif %},
                "domain":[""",
          """        {# multi-location: the legacy geosite rule below would pin Google/Netflix/... to the DEFAULT router entrance; country entrances need their own copy first #}
        {% if hconfigs['commercial_routing_enable'] and hconfigs['commercial_apply_to_xray'] and hconfigs.get('commercial_legacy_geosite_to_router', True) %}
        {% for loc in locations %}
        {
          "type":"field",
          "inboundTag": [{% for k in loc['tags'] %}"{{ loc['tags'][k] }}"{{ "," if not loop.last }}{% endfor %}],
          "outboundTag": "{{ loc['router_outbound'] }}",
          "domain":[
              "geosite:google", "geosite:netflix", "geosite:spotify", "geosite:nvidia", "geosite:bytedance", "geosite:tiktok",
              "geosite:unity", "geosite:reddit", "geosite:openai",
              "domain:ip.gs", "domain:kmplayer.com", "domain:ipinfo.io", "domain:openai.com", "domain:ai.com", "domain:freepik.com",
              "domain:developer.mozilla.org", "domain:behance.net", "domain:remini.com", "domain:snapchat.com", "domain:tunetank.com",
              "domain:livechart.me", "domain:9anime.me", "domain:pixelexperience.org", "domain:evolution-x.org", "domain:kenvyra.xyz",
              "domain:spark-os.live", "domain:pixelos.net", "domain:gamebanana.com", "domain:myanimelist.net", "domain:sourcemod.net",
              "domain:alliedmods.net", "domain:pagespeed.web.dev", "domain:axieinfinity.com", "domain:clubhouse.com", "domain:omegle.com",
              "domain:alldatasheet.com", "domain:flaticon.com", "domain:xda-developers.com", "domain:giglio.com", "domain:lookmovie2.to",
              "domain:adbtc.top", "domain:homedepot.com", "domain:flutter.dev", "domain:adobe.com"
              {% if hconfigs['warp_mode'] != 'disable' %}
                {%for d in hconfigs.get('warp_sites','').split('\\n')%}
                ,"domain:{{d.strip()}}"
                {% endfor %}
              {% endif %}
          ]
        },
        {% endfor %}
        {% endif %}
         {
                "type":"field",
                "outboundTag": {% if hconfigs['commercial_routing_enable'] and hconfigs['commercial_apply_to_xray'] and hconfigs.get('commercial_legacy_geosite_to_router', True) %}"commercial-local-router"{% elif hconfigs['warp_mode'] == 'disable' %}"freedom"{% else %}"WARP"{% endif %},
                "domain":["""),
      (
          """        {
          "type": "field",
          "port": "0-65535",
          "outboundTag": {% if hconfigs['commercial_routing_enable'] and hconfigs['commercial_apply_to_xray'] %}"commercial-local-router"{% elif hconfigs['warp_mode'] == 'all' %}"WARP"{% else %}"freedom"{% endif %}
        }""",
          """        {# multi-location: every slot inbound is sent to its own router entrance (disabled slots end in a blocking entrance) #}
        {% if hconfigs['commercial_routing_enable'] and hconfigs['commercial_apply_to_xray'] %}
        {% for loc in locations %}
        {
          "type": "field",
          "inboundTag": [{% for k in loc['tags'] %}"{{ loc['tags'][k] }}"{{ "," if not loop.last }}{% endfor %}],
          "outboundTag": "{{ loc['router_outbound'] }}"
        },
        {% endfor %}
        {% endif %}
        {
          "type": "field",
          "port": "0-65535",
          "outboundTag": {% if hconfigs['commercial_routing_enable'] and hconfigs['commercial_apply_to_xray'] %}"commercial-local-router"{% elif hconfigs['warp_mode'] == 'all' %}"WARP"{% else %}"freedom"{% endif %}
        }"""))

patch("opt/hiddify-manager/xray/configs/06_outbounds.json.j2", (
    """        "port": {{ hconfigs['commercial_router_port']|int }}
      }
    }{% endif %}""",
    """        "port": {{ hconfigs['commercial_router_port']|int }}
      }
    }{% for loc in locations %},
    {
      "tag": "{{ loc['router_outbound'] }}",
      "protocol": "socks",
      "settings": {
        "address": "{{ hconfigs['commercial_router_host'] }}",
        "port": {{ loc['router_port'] }}
      }
    }{% endfor %}{% endif %}"""))

# ------------------------------------------------------------------------------------------------ haproxy templates
patch("opt/hiddify-manager/haproxy/haproxy.cfg.j2", (
    """{% include '/opt/hiddify-manager/haproxy/backends/v10-cdn.cfg.pj2' %}""",
    """{% include '/opt/hiddify-manager/haproxy/backends/v10-cdn.cfg.pj2' %}

{% include '/opt/hiddify-manager/haproxy/backends/v10-loc.cfg.pj2' %}"""))

patch("opt/hiddify-manager/haproxy/fronts/in_tcpmode.cfg.pj2", (
    """    default_backend to_httpmode""",
    """    {# multi-location: HTTP/1.1 (and fallback) routing of the slot inbounds by their secret random paths #}
    {% if hconfigs['core_type']=="xray" %}
    {% for loc in locations %}
        {% for k in loc['tags'] %}
            {% if hconfigs[loc['protos'][k]~'_enable'] and hconfigs[loc['streams'][k]~'_enable'] %}
    use_backend {{ loc['tags'][k] }} if { path_beg /{{ loc['paths'][k] }} }
            {% endif %}
        {% endfor %}
    {% endfor %}
    {% endif %}

    default_backend to_httpmode"""))

patch("opt/hiddify-manager/haproxy/maps/path_v10.j2", (
    """{% if hconfigs['vless_enable'] and hconfigs['xhttp_enable'] %}
/oqxc2dfs542AIsrnQrnb v10-vless-xhttp-cdn-http
{% endif %}

/{{hconfigs['proxy_path_client']}}/generate_204 generate_204""",
    """{% if hconfigs['vless_enable'] and hconfigs['xhttp_enable'] %}
/oqxc2dfs542AIsrnQrnb v10-vless-xhttp-cdn-http
{% endif %}

{# multi-location slot inbounds (secret random paths, prefix-checked at creation) #}
{% if hconfigs['core_type']=="xray" %}
{% for loc in locations %}
{% for k in loc['tags'] %}
{% if hconfigs[loc['protos'][k]~'_enable'] and hconfigs[loc['streams'][k]~'_enable'] %}
/{{ loc['paths'][k] }} {{ loc['tags'][k] }}-http
{% endif %}
{% endfor %}
{% endfor %}
{% endif %}

/{{hconfigs['proxy_path_client']}}/generate_204 generate_204"""))


# ------------------------------------------------------------------------------------------------ routing form fix (owner approved 2026-10-13): pairs live in formfix_pairs.py (shared with the production delta)
sys.path.insert(0, HERE)
from formfix_pairs import FORMFIX  # noqa: E402
from routercore_pairs import ROUTERCORE  # noqa: E402

for _rel, _pairs in list(FORMFIX.items()) + list(ROUTERCORE.items()):
    patch(_rel, *_pairs)


def _region(text, start, end, new, must_contain, rel):
    """replace the span [start, end) by `new`: both anchors must occur exactly once (after LF normalisation) and the replaced span must contain every
    `must_contain` guard string (so a wrong span can never be replaced silently). Line endings of the file are kept (pure CRLF or pure LF only)."""
    crlf = text.count("\r\n") > text.count("\n") / 2
    if crlf and text.count("\r\n") != text.count("\n"):
        sys.exit(f"PATCH FAILED {rel}: mixed line endings")
    t = text.replace("\r\n", "\n")
    if t.count(start) != 1:
        sys.exit(f"PATCH FAILED {rel}: start anchor occurs {t.count(start)} times\n---\n{start[:200]}")
    i = t.index(start)
    j = t.find(end, i + len(start))
    if j < 0:
        sys.exit(f"PATCH FAILED {rel}: end anchor not found after the start anchor\n---\n{end[:200]}")
    old = t[i:j]
    for m in must_contain:
        if m not in old:
            sys.exit(f"PATCH FAILED {rel}: the span to replace does not contain the guard {m!r}")
    t = t[:i] + new + t[j:]
    return t.replace("\n", "\r\n") if crlf else t


def _apply(text, old, new, rel):
    """exact, line-ending-preserving replace: the anchor must occur exactly once in LF or in CRLF form"""
    for style in ("\n", "\r\n"):
        o, n = old.replace("\n", style), new.replace("\n", style)
        c = text.count(o)
        if c == 1:
            return text.replace(o, n)
        if c > 1:
            sys.exit(f"PATCH FAILED {rel}: anchor occurs {c} times")
    sys.exit(f"PATCH FAILED {rel}: anchor not found (neither LF nor CRLF)\n---\n{old[:300]}")


def main():
    summary = []
    for rel, ops in PATCHES.items():
        with open(os.path.join(BASE, rel), encoding="utf-8", newline="") as f:
            src = f.read()
        text = src
        crlf = text.count("\r\n") > text.count("\n") / 2
        for op in ops:
            if op[0] == "APPEND":
                add = op[1].replace("\r\n", "\n")
                nl = "\r\n" if crlf else "\n"
                text = text.rstrip("\r\n") + nl + add.replace("\n", nl)
                continue
            if op[0] == "REGION":
                text = _region(text, op[1], op[2], op[3], op[4], rel)
                continue
            text = _apply(text, op[0], op[1], rel)
        dst = os.path.join(OUT, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        summary.append((rel.split("/")[-1], hashlib.sha256(src.encode()).hexdigest()[:12],
                        hashlib.sha256(text.encode()).hexdigest()[:12], "CRLF" if crlf else "LF"))
    for name, a, b, st in summary:
        print(f"{name:26s} {st:4s} base {a} -> new {b}")


if __name__ == "__main__":
    main()
