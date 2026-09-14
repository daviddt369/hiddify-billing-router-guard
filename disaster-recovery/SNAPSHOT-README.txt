VPN Ru Node — disaster-recovery snapshot
=========================================

This bundle was produced by disaster-recovery/snapshot.sh. It contains
everything that exists ONLY on the server it was captured from and nowhere
else (git included). COPY IT OFF THAT SERVER IMMEDIATELY — a snapshot that
only lives on the box it protects against is worthless.

CONTAINS LIVE SECRETS. Store it like the most sensitive thing in this
project: encrypted at rest, never in git, never in a public location.

Contents:
- mariadb-full-dump.sql          — full DB (users, subscriptions, commercial
                                    routing, anti-share, everything).
- selectel-templates/*.tar.gz    — Selectel custom CDN XHTTP integration
                                    files. These exist nowhere else — not in
                                    git, not in any stock Hiddify backup.
- secrets/panel-secrets.env      — Hiddify panel secrets (Telegram etc).
- secrets/file-referenced-keys.tar.gz — any file:-referenced secrets found
                                    in the DB dump (e.g. WireGuard private
                                    keys). Empty/absent if none were found.
- secrets/acme-certs.tar.gz      — TLS certs+keys for every domain (acme.sh).
- manifest.json                  — capture metadata (timestamp, hiddifypanel
                                    version, what was/wasn't found).

To restore onto a fresh server:
  1. sudo bash disaster-recovery/bootstrap.sh      (fresh VPS, platform only)
  2. sudo bash disaster-recovery/restore.sh <this-bundle-dir>
  3. Manually repoint DNS + Gcore/Selectel CDN origin to the new server IP
     (this lives in those providers' own dashboards, not in this bundle).
  4. Re-run a smoke test through the CDN path.

This is a point-in-time snapshot — it goes stale as new users/traffic
accumulate. Re-run snapshot.sh regularly.
