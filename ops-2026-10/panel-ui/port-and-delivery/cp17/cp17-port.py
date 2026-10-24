"""CP-17 on the stand: name VPN Panel instead of Hiddify in titles / top bar / footer, no 'panel is outdated' notice, Russian text of the V2 dashboard, new theme version.
Backups of every touched file in /root/cp17-stand-bak first. Idempotent.   run in the stand as root:  python3 /work/stage2/cp17-port.py"""
import json
import os
import shutil

P = "/opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel"
BAK = "/root/cp17-stand-bak"
BRAND = "VPN Panel"


def edit(rel, pairs, must=True):
    path = os.path.join(P, rel)
    dst = os.path.join(BAK, rel)
    if not os.path.exists(dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(path, dst)
    s = open(path, encoding="utf-8").read()
    for old, new in pairs:
        if new in s and old not in s:
            continue
        assert old in s, f"{rel}: pattern not found: {old[:60]}"
        s = s.replace(old, new, 1)
    open(path, "w", encoding="utf-8").write(s)


# 1) the panel is a frozen fork: never 'outdated' (also stops the version lookup on GitHub that the check did on every page)
edit("hutils/utils.py", [("def is_panel_outdated() -> bool:\n    # TODO: handle beta and develop version too\n",
                          "def is_panel_outdated() -> bool:\n    return False  # frozen custom fork: upstream updates are never applied, the 'outdated' notice is meaningless\n    # TODO: handle beta and develop version too\n")])

# 2) names
edit("panel/admin/templates/admin_v2.html", [("<title>HiddifyManager</title>", f"<title>{BRAND}</title>")])
edit("static/admin-v2/index.html", [("<title>HiddifyManager</title>", f"<title>{BRAND}</title>")])
edit("templates/admin-layout.html", [('<span class="brand-text font-weight-light">{{_("master.page-title")}}', f'<span class="brand-text font-weight-light">{BRAND}'),
                                     ("css/panel-theme.css', version=1", "css/panel-theme.css', version=3")])
edit("templates/master.html", [('<title>{% block title %}{% endblock %} | {{_("master.page-title")}} | {{version}}</title>', f"<title>{{% block title %}}{{% endblock %}} | {BRAND}</title>"),
                               ('© 2025 Hiddify {{"Central" if hutils.node.is_parent() else\n            ""}} <span class="badge">{{version}}</span>', f"© {BRAND}")])

# 3) the SSH notice of the V2 dashboard in Russian (the panel text only exists in the languages of the stock translations)
edit("panel/admin/v2_menu.py", [('"summary": _("serverssh.password-login.warning"),',
                                 '"summary": "Сервер уязвим для подбора пароля: в SSH включён вход по паролю. Для защиты перейдите на вход по ключу и отключите парольную аутентификацию.",')])

# 4) the V2 app: brand + Russian texts in the English message set (the app has no Russian set of its own; the panel language of this admin is English, so these are the texts shown)
jp = os.path.join(P, "static/admin-v2/assets/index.js")
dst = os.path.join(BAK, "static/admin-v2/assets/index.js")
if not os.path.exists(dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(jp, dst)
s = open(jp, encoding="utf-8").read()
i = s.index("Traffic, users and server health at a glance") if "Traffic, users and server health at a glance" in s else -1
if i != -1:
    a = s.rfind("JSON.parse('", 0, i) + len("JSON.parse('")
    b = s.index("')", i)
    d = json.loads(s[a:b])
    ru = json.load(open("/work/stage2/cp17-ru.json", encoding="utf-8"))
    n = 0
    for k, v in ru.items():
        node = d
        parts = k.split(".")
        for p_ in parts[:-1]:
            node = node[p_]
        if parts[-1] in node:
            node[parts[-1]] = v
            n += 1
        else:
            print("missing key:", k)
    lit = json.dumps(d, ensure_ascii=False, separators=(",", ":")).replace("\\", "\\\\").replace("'", "\\'")
    s = s[:a] + lit + s[b:]
    print("translated keys:", n)
s = s.replace('"page-title":"HiddifyManager"', f'"page-title":"{BRAND}"') if False else s
open(jp, "w", encoding="utf-8").write(s)
shutil.chown(jp, "hiddify-panel", "hiddify-panel")
print("cp17 ported; backups in", BAK)
