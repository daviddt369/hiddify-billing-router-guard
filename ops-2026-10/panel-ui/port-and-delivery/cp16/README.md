# CP-16 (trial, STAND ONLY): Hiddify 13 "Admin V2" shell + dashboard on the 12.0.0 custom panel

Source: the NL node (Hiddify 13.0.3), read only: `static/admin-v2` (Vue 3 + PrimeVue single page app, 16 MB), `panel/admin/{v2_view,v2_menu}.py`, `templates/admin_v2.html`, `panel/commercial/restapi/v2/{pydantic_schema.py,admin/dashboard_*.py}`,
`hutils/{node_system,system_metrics,usage_cache,usage_stats}.py` and `DailyUsage.get_dashboard_stats`.

`cp16-port.py` (run in the stand as root) copies them in and fixes the version skew: `g` / `current_app` taken from flask (13 exports them from the package root), `system_metrics` imported explicitly,
no `User.deleted` in 12.0.0, `from __future__ import annotations` in models/usage.py; adds the two dashboard routes to the admin REST blueprint and `register_v2_routes(app, admin_bp)` to panel/admin/__init__.py. Backups of the touched files: /root/cp16-stand-bak in the stand.
`cp16-probe.py` / `cp16-direct.py`: HTTP probe with an admin session / direct calls of the three building blocks.

Result (2026-10-23): /admin/v2/ renders with real stand data (light and dark theme), /api/v2/admin/dashboard/ and /dashboard/disk/ answer 200.
Still to do before production: menu (our pages, without the Proxy Editor / Utils entries that need the proxy_v3 engine), the "outdated" notices, Russian labels, landing redirect, tests, delta package, RU rollout.
