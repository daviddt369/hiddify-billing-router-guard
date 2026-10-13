# Multi-location addon (source and tests)

A multi-location layer for the Hiddify-based stack of this repository: the subscription offers **AUTO** plus one entry per configured **location**, every location has its own
entrance and balancer in the `xray-router`, and the admin panel manages locations, their nodes and one common **Apply** button.

This directory is a **sanitized source snapshot** (status: v5). It is not a drop-in installer: see "What is not here".

## What it adds

| Area | What |
|---|---|
| Subscription | `…/<user link>/happ/` for Happ / INCY: one config per location (AUTO inside = all protocols, fastest by ping); the usual link of a user is not changed for anybody |
| Router | one socks entrance and one `leastPing` balancer per location (`hutils/multiloc_router.py`); a location whose nodes are all dead fails closed |
| Admin | page «Страны / локации» (titles, flags, nodes, visibility, testers), one **Apply** bar on every routing page (`multiloc_apply.py`) |
| Apply | the bar compares the router with the database and does the minimum: nothing / the locations layer / the full pipeline - in the background, one router restart |
| Full cycle | the legacy pipeline runs in a private copy of the router directory; its restarts are deferred to ONE at the end (`multiloc_full.py`) |
| Rule compaction | neighbouring single-entry rules with the same target are joined (`router_compact.py`): same routing, `xray -test` in 0.3 s instead of ~2 min, router start in seconds instead of minutes |
| `xray -test` shim | inside the full cycle the legacy layers' own `xray -test` (120 s limit) is answered by a test of a slim copy |
| Keep for decision | the finished config is compared with the running one before it replaces it (`router_diff.py`); differences the owner's change does not explain are kept and the panel asks («Применить новый конфиг» / «Не применять») |
| RoscomVPN in JSON | Happ / INCY get the RoscomVPN routing profile (header + rules inside every JSON config), optional RU zones + banks forced direct on the device |
| Routing form fix | see `release/…/commercial_routing.py` / `BusinessAdmin.py` and `tests/test_routing_form_rules.py` in the repository root |

## Layout

```
installer/   multiloc_installer.py (apply / rollback / verify), multiloc_delta_deploy.py (file-level delta with backups),
             build_payload.py + formfix_pairs.py + routercore_pairs.py (the exact replacements applied to the stock files),
             build_manifest.py, make_diffs.py, install.sh
new-files/   the files this addon adds (full text); DESTINATIONS.md maps them to the server paths:
             panel-overlay/hiddifypanel/…, manager-scripts/ (the two console scripts), haproxy / xray templates
diffs/       unified diffs of the stock / production files it modifies (INDEX.md lists them)
tests/       the test suite (real panel, real database, real xray on a prod-like stand) + stand_lib.py
stand-helpers/  stand helpers: dev_redeploy.sh, run_suite.sh, run_all.sh, golden.py, perf_*.py ...
```

## Tests

The suites need a **prod-like stand** (a throw-away VM / WSL distro with the panel, MariaDB, Redis, HAProxy, `xray`, `xray-router`) - they drive the real panel over HTTPS, press the
real buttons and read the real router. They do not run on a plain checkout. Overview: `test_01` pure functions, `test_02` subscriptions, `test_03` data plane, `test_04`/`05`/`06` lifecycle,
migration and installer, `test_07` pages, `test_09`/`13`/`14`/`15` Apply and the full cycle, `test_16` profile field, `test_17` keep-for-decision, `test_18` rule compaction
(differential test of two real xray routers on thousands of destinations), `test_19` routing form, `test_20` upstream outbounds.
`tests/fixtures/roscom/geo/` (the RoscomVPN `geoip.dat` / `geosite.dat`) is not included; fetch the files named in `tests/fixtures/roscom/*.DEEPLINK`.

## What is not here (on purpose)

This repository is public and `SECURITY.md` forbids real server addresses, domains, keys and personal data, so the snapshot has **no** deployment journal, runbooks with server details, production
reconnaissance scripts, logs, database dumps, geo data files or full copies of the stock files that carry a hard-coded contact. Placeholders / neutral wording replace the few strings that named
the installation. Because of that the published files are **not byte-identical** to what runs in production (the manifest and hashes are not published) - rebuild a deployable package from
`installer/` + the stock files of your own server.
