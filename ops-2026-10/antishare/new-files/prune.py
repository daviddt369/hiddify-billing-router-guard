"""Retention for the anti-share event table.

`cycle_summary` is written for EVERY enabled user on EVERY cycle (about 4,700 rows a day, 296 MB after five months) and nothing ever removed it. The rows that matter
(state transitions, bans, manual resets) are few hundreds in total. This keeps the diagnostic cycle rows for `summary_days` days and everything else for `other_days`.
Deletes in small batches so that the anti-share cycle and the panel are never blocked for long.

  python -m hiddifypanel.antishare.prune [--dry-run] [--summary-days 14] [--other-days 365]
"""
from __future__ import annotations

import argparse
import datetime
import time

from loguru import logger
from sqlalchemy import text

from hiddifypanel import create_app
from hiddifypanel.database import db

CYCLE_TYPES = ("cycle_summary", "cycle_skipped_disabled")
BATCH = 5000


def counts(now: datetime.datetime, summary_days: int, other_days: int) -> dict:
    s_cut = now - datetime.timedelta(days=summary_days)
    o_cut = now - datetime.timedelta(days=other_days)
    q = lambda sql, **kw: int(db.session.execute(text(sql), kw).scalar() or 0)       # noqa: E731
    return {
        "total": q("select count(*) from anti_share_event"),
        "cycle_old": q("select count(*) from anti_share_event where event_type in ('cycle_summary','cycle_skipped_disabled') and created_at < :c", c=s_cut),
        "other_old": q("select count(*) from anti_share_event where event_type not in ('cycle_summary','cycle_skipped_disabled') and created_at < :c", c=o_cut),
    }


def prune(summary_days: int = 14, other_days: int = 365, dry_run: bool = False, sleep: float = 0.2) -> dict:
    now = datetime.datetime.utcnow()
    c = counts(now, summary_days, other_days)
    logger.info("anti-share prune: {} rows, {} old cycle rows, {} old other rows (dry_run={})", c["total"], c["cycle_old"], c["other_old"], dry_run)
    if dry_run:
        return c
    s_cut = now - datetime.timedelta(days=summary_days)
    o_cut = now - datetime.timedelta(days=other_days)
    deleted = 0
    for where, cut in (("event_type in ('cycle_summary','cycle_skipped_disabled') and created_at < :c", s_cut),
                       ("event_type not in ('cycle_summary','cycle_skipped_disabled') and created_at < :c", o_cut)):
        while True:
            res = db.session.execute(text(f"delete from anti_share_event where {where} limit {BATCH}"), {"c": cut})
            db.session.commit()
            n = res.rowcount or 0
            deleted += n
            if n < BATCH:
                break
            time.sleep(sleep)
    c["deleted"] = deleted
    logger.info("anti-share prune: deleted {} rows", deleted)
    return c


def main() -> int:
    ap = argparse.ArgumentParser(description="Prune old anti-share events")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--summary-days", type=int, default=14)
    ap.add_argument("--other-days", type=int, default=365)
    a = ap.parse_args()
    app = create_app(app_mode="cli")
    with app.app_context():
        print(prune(a.summary_days, a.other_days, a.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
