# Disaster recovery: migrating to a new server

What to do if the hosting provider blocks/loses the current VPS and the
deployment needs to come back up on a different server as fast as possible.

See `README.ru.md` for the full walkthrough (this project's primary docs are
Russian-first). Quick reference:

```bash
# On the CURRENT server, regularly:
sudo bash disaster-recovery/snapshot.sh
# Copy the resulting bundle directory OFF this server immediately.

# On a FRESH Ubuntu 22.04/24.04 VPS:
git clone <this-repo-url> && cd hiddify-billing-router-guard
sudo bash disaster-recovery/bootstrap.sh          # platform only, no data
sudo bash disaster-recovery/restore.sh <bundle>   # restores DB/secrets/certs

# Manual, cannot be scripted from here:
#   - point DNS at the new server
#   - update Gcore/Selectel CDN origin to the new server's IP
#   - re-run a smoke test through the CDN path
```

**Rehearsed end-to-end on a real throwaway VPS on 2026-09-14** — `bootstrap.sh`
+ `restore.sh` with a real production snapshot (real users, secrets, keys,
certs) came up healthy and passed a direct smoke test. The CDN path (Gcore/
Selectel) was not exercised in that rehearsal — only the direct/origin path.
See `AGENT_COORDINATION_VPN.md` for details and the bugs found/fixed along
the way.

Files: `common-dr.sh` (shared helpers), `snapshot.sh` (capture), `bootstrap.sh`
(fresh-VPS platform install), `restore.sh` (data restore),
`selectel-template/` (Selectel CDN templates with the real secret path
parameterized out — never stored in git).
