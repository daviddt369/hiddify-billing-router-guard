# panel-ui: the admin panel rework of October 2026 (CP-15 .. CP-19)

Depersonalized code of the admin-panel work on a Hiddify 12.0.0 based RU entrance node. No real addresses, domains, keys or production outputs are stored here.
The product name shown by the panel is replaced by `VPN Panel` in this copy (the deployed panel uses its own brand string in the same places).

## What it does

| step | what |
|---|---|
| CP-15 | Happ / INCY page (RoscomVPN profile, own domains, apps that stay outside the VPN), bulk saving of location slots (`RoutingLocationAdmin.py`, `multiloc.py`) |
| CP-16 | Hiddify 13 "Admin V2" shell + dashboard ported onto 12.0.0 (data from the panel itself), our grouped Russian menu, the "node status" card (`nodes_status_api.py`, `nodes-widget.js`), one theme for every old page (`panel-theme.css`, light / dark) |
| CP-17 | the "panel is outdated" notice removed (the fork never takes upstream updates), product name in titles / top bar / footer, V2 dashboard texts in Russian (`cp17-ru.json`) |
| CP-18 | one collapsed "Instruction" panel per page (`templates/pt-faq.html`, current texts), router apply log folded away, **editable Telegram bot texts** (`hutils/tg_texts.py`: 29 phrases, `{placeholders}`, empty = built-in text, hot-reloaded) |
| CP-19 | readable tables / highlighted rows / server log in both themes, compact Happ page, **trial GB / days / devices of the bot are settings** (real account limits, not only text), no "install the panel" prompt in the admin area |

## Layout

- `files/` - our own files whole, as they sit under `hiddifypanel/` on the server (hutils, templates, panel/admin, static).
- `patches/` - Hiddify's own files that we changed (`admin-layout.html`, `master.html`, `hutils/utils.py`, `telegrambot/Usage.py`, `BusinessAdmin.py`) as unified diffs: they are not redistributed whole.
- `port-and-delivery/` - `cp16/cp16-port.py` copies the Admin V2 build and the dashboard API **from your own Hiddify 13 panel** (read only; the 16 MB compiled SPA and the ported code are deliberately NOT in this repository), fixes the version skew and registers the routes; `cp17/cp17-port.py` + `cp17-ru.json` translate the SPA texts; `make_pkg.py` of each step builds a delta package for `ops-2026-10/multiloc-layer/multiloc_delta_deploy.py` (a manifest entry per file with `base` / `new` sha256); `ru-check.py` is the post-deployment GET-only check (authenticates with the admin's `Hiddify-API-Key`, never requests POST actions); `cp19-rehearse.sh` is the stand rehearsal.
- `tests/` - `test_22_bot_texts.py` (+ `bot_trial_runner.py`, which creates the trial account exactly as the bot does and deletes it).

## How it was delivered

Every step went to a disposable stand first (a copy of the node), then to the node with `multiloc_delta_deploy.py --apply --backup <dir>`: the tool refuses before the first change when a live file is neither what the previous package installed nor the stock original, restores everything on any failure and restarts only the panel service.
Traps worth knowing: the delta tool's record (`STATE.json`) can lag behind hand-made changes (adopt the live hash with a backed-up one-line edit); a file on the node can be newer than the one on the stand (compare hashes first); GET-checking `/admin/actions/*` triggers nothing only because the actions are POST - the check script skips them anyway.

## Not included on purpose

The compiled Admin V2 SPA and the Hiddify 13 code it depends on (port them from your own panel), raw production outputs, screenshots, backups.
