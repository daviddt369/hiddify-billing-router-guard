"""Reset of the anti-share observations ("start the statistics from zero").

What is cleared: the observed events (cycle rows, state transitions, bans), the per-user address profiles, and the per-user state (score, warned/blocked marks, bans).
What is kept: the settings, the per-user "anti-share off" switches, the list of shared (CDN) addresses, the users' traffic bookkeeping (`last_cycle_usage`: zeroing it
would make the next cycle read a user's whole traffic as one burst) and the end of the learning period (nobody is sent back to "learning").
Before anything is deleted the three tables are copied to `anti_share_bak_<UTC time>_<name>`; only the newest copy is kept (the older copy is dropped).
Active nft bans of the cleared profiles are lifted.

  python -m hiddifypanel.antishare.reset [--yes]          (run as the panel user: the nft helper is allowed for it through sudo)
"""
from __future__ import annotations

import argparse
import datetime

from loguru import logger
from sqlalchemy import text

from hiddifypanel.database import db

TABLES = {"event": "anti_share_event", "profile": "anti_share_ip_profile", "state": "anti_share_state"}
BAK_PREFIX = "anti_share_bak_"


def _old_backups() -> list:
    rows = db.session.execute(text("select table_name from information_schema.tables where table_schema = database() and table_name like :p"), {"p": BAK_PREFIX + "%"})
    return [r[0] for r in rows]


def reset_all(backend=None) -> dict:
    now = datetime.datetime.utcnow()
    stamp = now.strftime("%Y%m%d_%H%M%S")
    old = _old_backups()
    out = {"stamp": stamp, "backup": {}, "unbanned": 0}

    # 1) copies first: if any of them fails nothing is deleted
    for key, table in TABLES.items():
        bak = f"{BAK_PREFIX}{stamp}_{key}"
        db.session.execute(text(f"create table `{bak}` like `{table}`"))
        db.session.execute(text(f"insert into `{bak}` select * from `{table}`"))
        n = db.session.execute(text(f"select count(*) from `{bak}`")).scalar()
        m = db.session.execute(text(f"select count(*) from `{table}`")).scalar()
        if int(n) < int(m):                                   # the cycle may add rows meanwhile, never fewer in the copy than were there at the start of the copy
            raise RuntimeError(f"copy of {table} is incomplete ({n} of {m}): nothing was deleted")
        out["backup"][key] = bak
        out[f"{key}_rows"] = int(n)
    db.session.commit()

    # 2) lift the bans that are still active (the nft sets also expire by themselves, this is for "now")
    if backend is not None:
        rows = db.session.execute(text("select distinct ip from anti_share_ip_profile where last_ban_until is not null and last_ban_until > :n"), {"n": now}).fetchall()
        for (ip,) in rows:
            try:
                backend.unban_ip(ip)
                out["unbanned"] += 1
            except Exception as exc:                        # one stubborn address must not stop the reset
                logger.warning("anti-share reset: could not unban {}: {}", ip, exc)

    # 3) clear
    db.session.execute(text("truncate table anti_share_event"))
    db.session.execute(text("truncate table anti_share_ip_profile"))
    db.session.execute(text(
        "update anti_share_state set score = 0, state = 'normal', violation_started_at = null, warned_at = null, blocked_at = null, ban_until = null, "
        "current_ip_count = 0, traffic_multiplier = 1, last_ips_snapshot = '[]' where state <> 'learning'"))
    db.session.execute(text(
        "update anti_share_state set score = 0, violation_started_at = null, warned_at = null, blocked_at = null, ban_until = null, "
        "current_ip_count = 0, traffic_multiplier = 1, last_ips_snapshot = '[]' where state = 'learning'"))
    db.session.commit()

    # 4) only the newest copy stays
    for t in old:
        db.session.execute(text(f"drop table if exists `{t}`"))
    db.session.commit()
    logger.info("anti-share reset: {}", out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="really do it (without it only the numbers are shown)")
    a = ap.parse_args()
    from hiddifypanel import create_app
    from hiddifypanel.antishare.config import AntiShareSettings
    from hiddifypanel.antishare.nftables import NftBanBackend
    app = create_app(app_mode="cli")
    with app.app_context():
        if not a.yes:
            for key, table in TABLES.items():
                print(table, db.session.execute(text(f"select count(*) from `{table}`")).scalar())
            print("(add --yes to reset)")
            return 0
        s = AntiShareSettings.load()
        print(reset_all(NftBanBackend(helper_path=s.nft_helper, enabled=s.nft_enabled, dry_run=s.nft_dry_run)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
