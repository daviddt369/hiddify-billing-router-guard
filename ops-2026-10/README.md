# ops-2026-10: hardening, monitoring, dynamic AUTO and anti-share

Depersonalized code and delivery scripts of the October 2026 operations work on a Hiddify-based RU entrance node with DE / NL exits.
No real addresses, domains, keys or outputs from production are stored here: addresses are documentation placeholders (`203.0.113.x`, `198.51.100.x`),
the directories `bin/` hold the apply / check / rollback scripts (the name `scripts/` is ignored by this repository's `.gitignore`).

Каждый пакет самодостаточен: бэкап перед изменением, проверки до и после, автоматический откат. Адреса в примерах — документационные.

| directory | what it is |
|---|---|
| `monitoring/` | `vpn_watch.py` - stdlib monitor (services, country canaries, WireGuard, certificates, ports, DNS errors, per-node probes) with Telegram delivery through the panel's bot. Two-tier paging: `notify_only` (critical, after 3 minutes) and `notify_slow` (important, after N minutes); everything else only goes to the daily summary. Tests, systemd units, config examples. |
| `auto-director/` | the AUTO controller: country-level choice with hysteresis (margin, confirmation cycles, dwell time, smoothing), override through the Xray API without a restart, fail-closed when both countries are down. Delivery scripts, unit tests, the diffs of the layer. |
| `multiloc-layer/` | the current multi-location layer files (`multiloc_router.py`, `multiloc.py`, `multiloc_installer.py`, `multiloc_delta_deploy.py`) with fail-closed AUTO / countries, the director hook and the client probe settings. Supersedes the same files of `multiloc/` when both are used. |
| `antishare/` | anti-share changes: a CDN edge takes one slot of the user's limit but is never banned (list in a file and in the admin page), retention for the events table, "clear all statistics" button with a copy kept in the database. New files in full, stock files as diffs, build scripts, units. |
| `client-profiles/` | what the `/happ/` link serves to Happ / INCY: the RU zone as `domain:` entries (INCY ignored the `regexp:` form in its routing) and the Android per-app bypass headers (apps that refuse to run under a VPN). Diffs, package-name checker, example list. |
| `hardening/` | applied fixes: `cp01` secret file permissions, `cp05` fail-closed routing, `cp07` WireGuard SNAT, `cp09` first-hop DNS over direct DoH, `cp11` panel code owned by root, `geo-update` geo data refresh with automatic revert. |

Every package follows the same shape: `apply.sh` (backup first, compile / import / config checks before any restart, automatic revert), `check-before.sh`, `check-after.sh`, `rollback.sh`.
Run them as root on the node; read the README of the package first. Packages were rehearsed on a disposable stand before they were applied.

Not included on purpose: packages that were prepared but not applied (AdGuard hardening, exit auto-update switch, node clean-up), raw outputs of the production runs, snapshots of production files and anything that names the installation.
