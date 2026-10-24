"""CP-16 trial on the STAND only: port the Hiddify 13 Admin V2 shell + dashboard (copied from NL) into the 12.0.0 package. Backups of every touched file first.
run inside the stand as root:  python3 /work/stage2/cp16-port.py"""
import os
import re
import shutil
import sys

P = "/opt/hiddify-manager/.venv313/lib/python3.13/site-packages/hiddifypanel"
S = "/work/stage2/cp16"
BAK = "/root/cp16-stand-bak"
os.makedirs(BAK, exist_ok=True)


def backup(rel):
    dst = os.path.join(BAK, rel)
    if not os.path.exists(dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(P, rel), dst)


def put(src, rel):
    dst = os.path.join(P, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    shutil.chown(dst, "hiddify-panel", "hiddify-panel")
    os.chmod(dst, 0o644)


# 1) new files (nothing of the 12.0.0 package is replaced)
for n in ("node_system.py", "system_metrics.py", "usage_cache.py", "usage_stats.py"):
    put(f"{S}/hutils/{n}", f"hutils/{n}")
for n in ("dashboard_api.py", "dashboard_schema.py"):
    put(f"{S}/restapi/{n}", f"panel/commercial/restapi/v2/admin/{n}")
for n in ("v2_view.py", "v2_menu.py"):
    put(f"{S}/admin/{n}", f"panel/admin/{n}")
put(f"{S}/restapi_v2/pydantic_schema.py", "panel/commercial/restapi/v2/pydantic_schema.py")
put(f"{S}/admin/admin_v2.html", "panel/admin/templates/admin_v2.html")

# 12.0.0 does not export `g` / `current_app` from the package root (13 does): the ported files take the plain Flask proxies
FIXES = [("from hiddifypanel import current_app as app\n", "from flask import current_app as app\n"),
         ("from hiddifypanel import g, hutils\n", "from flask import g\nfrom hiddifypanel import hutils\n"),
         ("from hiddifypanel import g\n", "from flask import g\n")]
for rel in ("panel/commercial/restapi/v2/admin/dashboard_api.py", "panel/admin/v2_menu.py", "panel/admin/v2_view.py"):
    path = os.path.join(P, rel)
    s = open(path, encoding="utf-8").read()
    for a, b in FIXES:
        s = s.replace(a, b)
    open(path, "w", encoding="utf-8").write(s)
dst = os.path.join(P, "static/admin-v2")
shutil.rmtree(dst, ignore_errors=True)
shutil.copytree(f"{S}/admin-v2", dst)
for d, _, fs in os.walk(dst):
    shutil.chown(d, "hiddify-panel", "hiddify-panel")
    for f in fs:
        shutil.chown(os.path.join(d, f), "hiddify-panel", "hiddify-panel")

# 2) usage model: the dashboard statistics method of 13
rel = "models/usage.py"
backup(rel)
nl = open(f"{S}/models/usage_nl.py", encoding="utf-8").read()
a = nl.index("    @staticmethod\n    def get_dashboard_stats")
b = nl.index("class UnsyncedUsage")
method = nl[a:b].rstrip() + "\n"
cur = open(os.path.join(P, rel), encoding="utf-8").read()
if "get_dashboard_stats" not in cur:
    if "from __future__ import annotations" not in cur:
        cur = "from __future__ import annotations\n" + cur
    method = method.replace(", User.deleted.is_(False)", "")          # 12.0.0 has no soft delete of users
    cur = cur.rstrip() + "\n\n" + method
    open(os.path.join(P, rel), "w", encoding="utf-8").write(cur)

# node_system: 13 imports system_metrics in hutils/__init__, 12.0.0 does not
path = os.path.join(P, "hutils/node_system.py")
s = open(path, encoding="utf-8").read()
if "from hiddifypanel.hutils import system_metrics" not in s:
    s = s.replace("from hiddifypanel import hutils\n", "from hiddifypanel import hutils\nfrom hiddifypanel.hutils import system_metrics\n", 1).replace("hutils.system_metrics.", "system_metrics.")
    open(path, "w", encoding="utf-8").write(s)

# 3) the REST API: dashboard routes
rel = "panel/commercial/restapi/v2/admin/__init__.py"
backup(rel)
cur = open(os.path.join(P, rel), encoding="utf-8").read()
if "AdminDashboardApi" not in cur:
    cur = cur.replace("        from .system_actions import UpdateUserUsageApi, AllConfigsApi\n",
                      "        from .system_actions import UpdateUserUsageApi, AllConfigsApi\n        from .dashboard_api import AdminDashboardApi, AdminDashboardDiskApi\n", 1)
    assert "AdminDashboardApi" in cur
    cur = cur.replace("        bp.add_url_rule('/me/', view_func=AdminInfoApi)  # type: ignore\n",
                      "        bp.add_url_rule('/me/', view_func=AdminInfoApi)  # type: ignore\n"
                      "        bp.add_url_rule('/dashboard/', view_func=AdminDashboardApi)  # type: ignore\n"
                      "        bp.add_url_rule('/dashboard/disk/', view_func=AdminDashboardDiskApi)  # type: ignore\n", 1)
    assert "/dashboard/disk/" in cur
    open(os.path.join(P, rel), "w", encoding="utf-8").write(cur)

# 4) the admin routes of the shell
rel = "panel/admin/__init__.py"
backup(rel)
cur = open(os.path.join(P, rel), encoding="utf-8").read()
if "register_v2_routes" not in cur:
    anchor = "    Backup.register(admin_bp)\n"
    assert anchor in cur
    cur = cur.replace(anchor, anchor + "    from .v2_view import register_v2_routes\n\n    register_v2_routes(app, admin_bp)\n", 1)
    open(os.path.join(P, rel), "w", encoding="utf-8").write(cur)

# our own grouped menu (text only for now) in place of the stock one; the stock builder stays as the fallback
mp = os.path.join(P, "panel/admin/v2_menu.py")
s = open(mp, encoding="utf-8").read()
if "_build_admin_v2_menu_stock" not in s:
    assert "def build_admin_v2_menu() -> list[dict]:" in s
    s = s.replace("def build_admin_v2_menu() -> list[dict]:", "def _build_admin_v2_menu_stock() -> list[dict]:", 1)
    s = s.rstrip() + "\n" + open(f"{S}/admin/menu_ru_append.py", encoding="utf-8").read()
    open(mp, "w", encoding="utf-8").write(s)

# the SPA's own first group (Dashboard / Utils / Proxy Editor): one item "Обзор"; Utils and the Proxy Editor need the 13 proxy engine that 12.0.0 does not have
jp = os.path.join(P, "static/admin-v2/assets/index.js")
if not os.path.exists(jp + ".orig"):
    shutil.copy2(jp, jp + ".orig")
s = open(jp, encoding="utf-8").read()
a = s.find('e=On(()=>[{label:n("menu.sectionNew")')
if a != -1:
    b = s.index("]}]),t=Kbt;", a) + len("]}]),")
    s = s[:a] + 'e=On(()=>[{label:"Обзор",items:[{label:"Обзор",icon:"pi pi-fw pi-home",to:"/"}]}]),' + s[b:]
    open(jp, "w", encoding="utf-8").write(s)
    shutil.chown(jp, "hiddify-panel", "hiddify-panel")
else:
    print("SPA menu already patched (or the pattern changed)")

# dashboard card "Состояние нод": the API + the small script next to the compiled app
put(f"{S}/restapi/nodes_status_api.py", "panel/commercial/restapi/v2/admin/nodes_status_api.py")
put(f"{S}/admin/nodes-widget.js", "static/admin-v2/nodes-widget.js")
rel = "panel/commercial/restapi/v2/admin/__init__.py"
cur = open(os.path.join(P, rel), encoding="utf-8").read()
if "AdminNodesStatusApi" not in cur:
    cur = cur.replace("        from .dashboard_api import AdminDashboardApi, AdminDashboardDiskApi\n",
                      "        from .dashboard_api import AdminDashboardApi, AdminDashboardDiskApi\n        from .nodes_status_api import AdminNodesStatusApi\n", 1)
    cur = cur.replace("        bp.add_url_rule('/dashboard/', view_func=AdminDashboardApi)  # type: ignore\n",
                      "        bp.add_url_rule('/dashboard/', view_func=AdminDashboardApi)  # type: ignore\n        bp.add_url_rule('/nodes-status/', view_func=AdminNodesStatusApi)  # type: ignore\n", 1)
    assert "AdminNodesStatusApi)" in cur and "nodes-status" in cur
    open(os.path.join(P, rel), "w", encoding="utf-8").write(cur)
tp = os.path.join(P, "panel/admin/templates/admin_v2.html")
s = open(tp, encoding="utf-8").read()
if "nodes-widget.js" not in s:
    s = s.replace('    <script type="module" crossorigin src="{{ static_js }}"></script>\n',
                  '    <script type="module" crossorigin src="{{ static_js }}"></script>\n    <script defer src="{{ static_js.rsplit(\'/\', 2)[0] }}/nodes-widget.js?v=1"></script>\n', 1)
    assert "nodes-widget.js" in s
    open(tp, "w", encoding="utf-8").write(s)

# one look for the old pages: the theme CSS, the shared sidebar (same menu as the V2 shell), jinja helpers
put(f"{S}/theme/panel-theme.css", "static/css/panel-theme.css")
vp = os.path.join(P, "panel/admin/v2_view.py")
s = open(vp, encoding="utf-8").read()
if "admin_v2_layout_menu" not in s:
    s = s.replace("from .v2_menu import build_admin_v2_menu, build_admin_v2_notices", "from .v2_menu import admin_v2_layout_menu, build_admin_v2_menu, build_admin_v2_notices", 1)
    s = s.replace("def register_v2_routes(flask_app, admin_bp):\n", "def register_v2_routes(flask_app, admin_bp):\n    flask_app.jinja_env.globals[\"admin_v2_layout_menu\"] = admin_v2_layout_menu\n\n", 1)
    assert "admin_v2_layout_menu" in s and 'globals["admin_v2_layout_menu"]' in s
    open(vp, "w", encoding="utf-8").write(s)
lp = os.path.join(P, "templates/admin-layout.html")
backup("templates/admin-layout.html")
s = open(lp, encoding="utf-8").read()
if "v2-sidebar" not in s:
    a = s.index('<aside id="main-sidebar"')
    b = s.index("</aside>", s.index("sub-sidebar", a)) + len("</aside>")
    s = s[:a] + open(f"{S}/theme/layout_sidebar.html", encoding="utf-8").read().strip() + s[b:]
    s = s.replace("{% block body_header %}{%endblock%}\n",
                  "{% block body_header %}{%endblock%}\n{% block head_tail %}<link rel=\"stylesheet\" href=\"{{ static_url_for(filename='css/panel-theme.css', version=1) }}\">{% endblock %}\n", 1)
    assert "panel-theme.css" in s
    open(lp, "w", encoding="utf-8").write(s)
    shutil.chown(lp, "hiddify-panel", "hiddify-panel")

for d in ("hutils", "models", "panel", "panel/admin", "panel/commercial/restapi/v2/admin"):
    shutil.rmtree(os.path.join(P, d, "__pycache__"), ignore_errors=True) if False else None
print("ported; backups in", BAK)
