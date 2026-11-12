"""backup_markers: a backup job's success marker must be fresh, otherwise vpn-watch raises a critical alert. Pure logic, no stand needed:
   python -m unittest test_backup_markers
"""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import vpn_watch as vw  # noqa: E402


def marker(age_hours):
    d = tempfile.mkdtemp(prefix="vw-bm-")
    p = os.path.join(d, "job.ok")
    open(p, "w").close()
    t = time.time() - age_hours * 3600
    os.utime(p, (t, t))
    return p


class BackupMarkers(unittest.TestCase):
    def cfg(self, path, limit=30):
        return {"backup_markers": [{"name": "db-daily", "file": path, "max_age_h": limit, "label": "daily DB backup"}]}

    def test_fresh_marker_is_ok(self):
        r = vw.check_backup_markers(self.cfg(marker(2)))
        self.assertEqual(len(r), 1)
        self.assertTrue(r[0].ok)

    def test_stale_marker_is_critical(self):
        r = vw.check_backup_markers(self.cfg(marker(31)))
        self.assertFalse(r[0].ok)
        self.assertEqual(r[0].level, vw.CRIT)
        self.assertIn("31.", r[0].detail)

    def test_missing_marker_is_critical(self):
        r = vw.check_backup_markers(self.cfg("/nonexistent/job.ok"))
        self.assertFalse(r[0].ok)
        self.assertEqual(r[0].level, vw.CRIT)
        self.assertIn("не выполнялся", r[0].detail)

    def test_limit_is_respected_per_job(self):
        r = vw.check_backup_markers({"backup_markers": [
            {"name": "a", "file": marker(100), "max_age_h": 110},
            {"name": "b", "file": marker(100), "max_age_h": 30},
        ]})
        self.assertTrue(r[0].ok)
        self.assertFalse(r[1].ok)

    def test_no_config_no_checks(self):
        self.assertEqual(vw.check_backup_markers({}), [])

    def test_stale_backup_pages_in_telegram_and_recovery_is_announced(self):
        """end to end with the real Monitor and a fake Telegram: a dead backup is NOT silent (critical level pages after 3 runs), and the fix is announced"""
        class FakeTg:
            def __init__(self):
                self.messages = []

            def send(self, text, silent=False):
                self.messages.append(text)
                return True
        tg = FakeTg()
        clock = {"t": 1_000_000.0}
        cfg = {"notify_levels": ["crit"], "thresholds": {"crit_runs": 3, "warn_runs": 5}}
        m = vw.Monitor(cfg, state_dir=tempfile.mkdtemp(prefix="vw-bm-"), now=lambda: clock["t"], tg=tg)
        path = marker(40)                                          # 40 h old, limit 30 h
        bcfg = self.cfg(path, 30)
        for _ in range(3):
            clock["t"] += 60
            alerts, recov = m.apply(vw.check_backup_markers(bcfg), "fast")
            m.notify(alerts, recov)
        self.assertEqual(len(tg.messages), 1, tg.messages)
        self.assertIn("daily DB backup", tg.messages[0])
        os.utime(path, None)                                       # the job succeeded: marker is fresh now
        clock["t"] += 60
        alerts, recov = m.apply(vw.check_backup_markers(bcfg), "fast")
        m.notify(alerts, recov)
        self.assertEqual(len(tg.messages), 2, tg.messages)         # the recovery notice

    def test_registered_in_run_fast(self):
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vpn_watch.py"), encoding="utf-8").read()
        self.assertIn("check_backup_markers(cfg)", src.split("def run_fast")[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
