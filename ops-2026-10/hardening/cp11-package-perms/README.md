# CP-11: root owns the code of the web panel (item 5)

Problem (RU, read-only check 2026-10-17): the panel package `.../site-packages/hiddifypanel` belongs to `hiddify-panel` (312 entries to depth 3; 118 `.py` writable by it, the directory too).
Root jobs import that code: `probe-routing-health.service` (User=root), `multiloc_ctl.py`, `commander.py`; sudoers: `hiddify-panel NOPASSWD commander.py *`.
A hole in the web panel = rewrite a module = code run as root.

Fix: `apply.sh` (backup = owner/mode list of every entry; chown -R root:root, chmod go-w, compileall by root, restart panel + background tasks, checks, auto-revert), `rollback.sh <backup dir>`.

Stand rehearsal 2026-10-17 (dev stand = same ownership as production): apply OK (all services active, no permission errors, panel user reads but cannot write, imports OK as panel user and root);
tests with the new owners: test_07_ui 8/8, test_13_apply 23/23, test_19_routing_form 5/5, test_02_subscription 33/37 (4 fails = fixture "30 Days" vs "24 Days" counter of stand test users, unrelated to rights);
rollback restored every owner and mode exactly. Production: NOT applied, waits for the owner's command.
