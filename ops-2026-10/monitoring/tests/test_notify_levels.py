"""notify_levels / notify_always: only the chosen levels are sent to Telegram. Pure logic, no stand needed:  python -m unittest test_notify_levels"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import vpn_watch as vw  # noqa: E402


class FakeTg:
    def __init__(self):
        self.messages = []

    def send(self, text, silent=False):
        self.messages.append(text)
        return True


def mon(cfg):
    tg = FakeTg()
    clock = {"t": 1_000_000.0}
    m = vw.Monitor(cfg, state_dir=tempfile.mkdtemp(prefix="vw-nl-"), now=lambda: clock["t"], tg=tg)
    return m, tg, clock


def run(m, tg, clock, results, times=1):
    for _ in range(times):
        clock["t"] += 60
        alerts, recov = m.apply(results, "fast")
        m.notify(alerts, recov)


CRIT_ONLY = {"notify_levels": ["crit"], "notify_always": ["routercfg:failclosed"], "thresholds": {"crit_runs": 3, "warn_runs": 5}}


class TestNotifyLevels(unittest.TestCase):
    def test_default_keeps_the_old_behaviour(self):
        m, tg, c = mon({"thresholds": {"crit_runs": 3, "warn_runs": 5}})
        run(m, tg, c, [vw.R("node:7", False, vw.WARN, "узел 7: не отвечает")], times=5)
        self.assertEqual(len(tg.messages), 1, tg.messages)

    def test_a_warning_is_silent_and_so_is_its_recovery(self):
        m, tg, c = mon(CRIT_ONLY)
        run(m, tg, c, [vw.R("node:7", False, vw.WARN, "узел 7: не отвечает")], times=20)
        run(m, tg, c, [vw.R("node:7", True, vw.WARN, "узел 7: ок")])
        self.assertEqual(tg.messages, [])
        self.assertFalse(m.state["checks"]["node:7"]["alerted"])

    def test_events_such_as_an_auto_switch_do_not_page(self):
        m, tg, c = mon(CRIT_ONLY)
        run(m, tg, c, [vw.R("director:switch", False, vw.INFO, "AUTO: DE → NL", event=True),
                       vw.R("router:restarted", False, vw.INFO, "xray-router перезапускался", event=True),
                       vw.R("drift:egress", False, vw.WARN, "изменилось", event=True)])
        self.assertEqual(tg.messages, [])

    def test_a_critical_failure_still_pages_once_and_recovers(self):
        m, tg, c = mon(CRIT_ONLY)
        bad = [vw.R("canary:DE", False, vw.CRIT, "DE: нет ответа")]
        run(m, tg, c, bad, times=2)
        self.assertEqual(tg.messages, [])
        run(m, tg, c, bad)
        self.assertEqual(len(tg.messages), 1)
        self.assertIn("🔴", tg.messages[0])
        run(m, tg, c, bad, times=3)
        self.assertEqual(len(tg.messages), 1, "no repeat spam")
        run(m, tg, c, [vw.R("canary:DE", True, vw.CRIT, "DE: выход ok")])
        self.assertEqual(len([x for x in tg.messages if "восстановлено" in x]), 1)

    def test_fail_closed_regression_is_always_sent_even_though_it_is_only_a_warning(self):
        m, tg, c = mon(CRIT_ONLY)
        run(m, tg, c, [vw.R("routercfg:failclosed:balancer-s1", False, vw.WARN, "balancer-s1: fallbackTag=None")], times=5)
        self.assertEqual(len(tg.messages), 1, tg.messages)

    def test_the_problem_is_still_visible_in_the_state_and_the_daily_summary(self):
        cfg = dict(CRIT_ONLY, daily_summary_hour=0)
        m, tg, c = mon(cfg)
        run(m, tg, c, [vw.R("node:7", False, vw.WARN, "узел 7: не отвечает")], times=6)
        self.assertFalse(m.state["checks"]["node:7"]["ok"])
        m.daily()
        self.assertTrue(any("сводка" in x and "узел 7" in x for x in tg.messages), tg.messages)


class TestSummaryAndDrift(unittest.TestCase):
    def test_events_are_not_counted_as_problems_in_the_daily_summary(self):
        m, tg, c = mon({"daily_summary_hour": 0})
        run(m, tg, c, [vw.R("drift:x", False, vw.WARN, "изменилось x", event=True)])
        m.daily()
        self.assertTrue(any("проблем 0" in x for x in tg.messages), tg.messages)

    def test_a_file_that_is_empty_for_a_moment_is_not_a_change(self):
        d = tempfile.mkdtemp()
        f = os.path.join(d, "cfg.json")
        open(f, "w").write('{"a":1}')
        cfg = {"fingerprints": [{"name": "t", "glob": f, "why": "w"}]}
        st = {}
        self.assertEqual(vw.check_fingerprints(cfg, st), [])
        open(f, "w").write("")                       # Hiddify is rewriting the file
        self.assertEqual(vw.check_fingerprints(cfg, st), [])
        open(f, "w").write('{"a":1}')                # the same content is back
        self.assertEqual(vw.check_fingerprints(cfg, st), [], "no false drift after the rewrite")
        open(f, "w").write('{"a":2}')
        self.assertEqual(len(vw.check_fingerprints(cfg, st)), 1, "a real change still shows")


ALLOW = {"notify_only": ["svc:", "port:", "canary:AUTO", "director:closed", "ports:new", "routercfg:failclosed"], "notify_slow": {"canary:DE": 10, "canary:NL": 10},
         "thresholds": {"crit_runs": 3, "warn_runs": 5}}


class TestAllowList(unittest.TestCase):
    def test_only_listed_checks_page_everything_else_is_silent_even_if_critical(self):
        m, tg, c = mon(ALLOW)
        run(m, tg, c, [vw.R("wg:wg0:rx", False, vw.CRIT, "wg0: тихо"), vw.R("tcp:DE-exit-443", False, vw.CRIT, "недоступен"),
                       vw.R("node:7", False, vw.CRIT, "узел"), vw.R("director:switch", False, vw.INFO, "AUTO: DE → NL", event=True)], times=30)
        self.assertEqual(tg.messages, [])

    def test_tier_one_pages_after_three_minutes_labelled_critical(self):
        m, tg, c = mon(ALLOW)
        bad = [vw.R("svc:hiddify-xray", False, vw.CRIT, "hiddify-xray: failed")]
        run(m, tg, c, bad, times=2)
        self.assertEqual(tg.messages, [])
        run(m, tg, c, bad)
        self.assertEqual(len(tg.messages), 1)
        self.assertIn("🔴 КРИТИЧНО:", tg.messages[0])

    def test_one_country_down_pages_only_after_ten_minutes_labelled_important(self):
        m, tg, c = mon(ALLOW)
        bad = [vw.R("canary:DE", False, vw.CRIT, "DE: нет ответа")]
        run(m, tg, c, bad, times=9)
        self.assertEqual(tg.messages, [], "an AUTO switch masks a short outage")
        run(m, tg, c, bad)
        self.assertEqual(len(tg.messages), 1)
        self.assertIn("🟠 ВАЖНО:", tg.messages[0])

    def test_auto_dead_is_tier_one(self):
        m, tg, c = mon(ALLOW)
        run(m, tg, c, [vw.R("canary:AUTO", False, vw.CRIT, "AUTO: нет ответа")], times=3)
        self.assertEqual(len(tg.messages), 1)
        self.assertIn("КРИТИЧНО", tg.messages[0])


if __name__ == "__main__":
    unittest.main()
