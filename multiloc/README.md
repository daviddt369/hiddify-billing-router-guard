# Multi-location addon (source and tests)

A multi-location layer for the Hiddify-based stack of this repository: the subscription offers **AUTO** plus one entry per configured **location**, every location has its own
entrance and balancer in the `xray-router`, and the admin panel manages locations, their nodes and one common **Apply** button.

This directory is a **sanitized source snapshot** (status: v5). A deployable package is rebuilt from it with one command (`installer/assemble_package.py`, see "Rebuild and install").

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

## Rebuild and install

You need a server with Hiddify 12 (venv `/opt/hiddify-manager/.venv313`), root and this repository.

1. **Base.** Run the installers in `release/` in the usual order (`release/clean-install-full-stack.sh`: business → routing → antishare). The addon goes on top of them.
2. **Assemble the package from THIS server's own files** (the server is only read, never changed):
   ```
   python3 multiloc/installer/assemble_package.py --out /root/multiloc-pkg
   ```
   It reads the 22 files the addon modifies, applies the exact replacements (`build_payload.py`), adds the 14 new files from `new-files/` (destinations: `new-files/DESTINATIONS.md`) and writes
   `MANIFEST.json`: the sha256 every file MUST have on the server before the install, and the sha256 it gets. If an anchor is not found (another Hiddify version, or a hand-edited file) the assembly
   stops and names the file and the place - nothing reached the server. `--tar file.tgz` writes the package as one file you can carry to another server of the same baseline.
3. **Install** (idempotent, every file backed up first, one-command rollback):
   ```
   /root/multiloc-pkg/install.sh preflight   # read-only checks and the plan
   /root/multiloc-pkg/install.sh apply       # files + 3 new DB tables + panel restart + xray / haproxy templates
   /root/multiloc-pkg/install.sh verify
   /root/multiloc-pkg/install.sh rollback    # to get back to how it was
   ```
   The router layer is switched on by the first **Apply** in the admin panel (Routing → Countries / locations); `apply` itself does not touch `xray-router`.
4. **Data** (nodes, locations, rules) lives in the database and the panel settings, not in this repository. It comes back with the full DB dump: `disaster-recovery/snapshot.sh` takes it, `disaster-recovery/restore.sh` puts it back. On a new server the order is `bootstrap.sh` → `restore.sh` → steps 2-3 above → **Apply** in the admin panel (see `disaster-recovery/README.md`).

What was verified on the stand: the replacements give the byte-identical result on the production files; on the 14 files the installers of this repository write (7 business, 5 routing, 2 xray templates)
every anchor is found, the result compiles and building a second time over its own output changes nothing; a package made by `assemble_package.py` agrees with its manifest. The other 8 files (Hiddify's
own files and the installation's own customizations - HAProxy templates, `commander.py`, ...) are read from your server at assembly time.

## Layout

```
installer/   assemble_package.py (package from the server's files), multiloc_installer.py (apply / rollback / verify), multiloc_delta_deploy.py (file-level delta with backups),
             build_payload.py + formfix_pairs.py + routercore_pairs.py (the exact replacements applied to the stock files; anchors tolerate trailing whitespace and more than one file version),
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
the installation. Because of that the published files are **not byte-identical** to what runs in production (the manifest and hashes are not published) - the package is assembled from `installer/`
and the files of your own server.
