"""auto-director: decision logic and daemon plumbing (pure Python, no Xray needed). Run: python -m unittest test_auto_director -v"""
import json
import os
import random
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import auto_director as ad  # noqa: E402

P = dict(ad.DEFAULTS)
CYCLE = 10.0


def run(decider, seq, t0=10_000.0, per=CYCLE):
    """feed (de, nl) latencies (None = failed cycle); returns [(index, target, reason)] of every switch"""
    out, t = [], t0
    for i, (de, nl) in enumerate(seq):
        before = decider.cur
        target, why = decider.step(t, {"DE": de, "NL": nl})
        if why and target != before:
            out.append((i, target, why))
        t += per
    return out


def fresh(cur=None, **over):
    p = dict(P, **over)
    d = ad.Decider(["DE", "NL"], p)
    if cur:
        d.cur, d.last_switch = cur, 0.0             # an old choice: the dwell time is long over
        d.ewma = {"DE": 50.0, "NL": 50.0}
    return d


class TestTheOwnersTable(unittest.TestCase):
    def test_45_vs_60_keeps_the_current_route(self):
        d = fresh("DE")
        self.assertEqual(run(d, [(45, 60)] * 30), [])
        d = fresh("NL")
        self.assertEqual(run(d, [(45, 60)] * 30), [])        # 15 ms is below the 20 ms margin: NL stays even though DE is faster

    def test_40_vs_70_selects_germany(self):
        d = fresh("NL")
        sw = run(d, [(40, 70)] * 40)
        self.assertEqual([s[1] for s in sw], ["DE"])

    def test_75_vs_42_selects_netherlands(self):
        d = fresh("DE")
        sw = run(d, [(75, 42)] * 40)
        self.assertEqual([s[1] for s in sw], ["NL"])

    def test_50_vs_55_keeps_the_current_route(self):
        for cur in ("DE", "NL"):
            self.assertEqual(run(fresh(cur), [(50, 55)] * 50), [])

    def test_current_unavailable_other_50_moves_to_the_other(self):
        d = fresh("DE")
        sw = run(d, [(None, 50)] * 6)
        self.assertEqual([s[1] for s in sw], ["NL"])
        self.assertIn("down", sw[0][2])

    def test_both_unavailable_is_block_and_it_recovers(self):
        d = fresh("DE")
        sw = run(d, [(None, None)] * 6)
        self.assertEqual([s[1] for s in sw], [ad.NONE])
        sw2 = run(d, [(None, 80)] * 4, t0=20_000.0)
        self.assertEqual([s[1] for s in sw2], ["NL"])         # a country answered twice in a row -> reopen on the one that answers
        sw3 = run(d, [(45, 80)], t0=30_000.0)
        self.assertEqual(sw3, [])                              # ... and no immediate hop to the faster one (confirmation + dwell)


class TestHysteresis(unittest.TestCase):
    def test_a_single_failed_cycle_switches_nothing(self):
        d = fresh("DE")
        self.assertEqual(run(d, [(50, 50)] * 5 + [(None, 50)] + [(50, 50)] * 5), [])
        self.assertEqual(run(fresh("DE"), [(50, 50)] * 3 + [(None, 50), (None, 50)] + [(50, 50)] * 5), [])     # two lost cycles are still below fail_cycles=3

    def test_detection_takes_about_three_cycles(self):
        d = fresh("DE")
        sw = run(d, [(50, 50)] * 3 + [(None, 50)] * 5)
        self.assertEqual(len(sw), 1)
        self.assertEqual(sw[0][0], 5)                           # the third failed cycle (index 3,4,5) -> 30 s at 10 s per cycle

    def test_jitter_around_equal_routes_never_switches(self):
        rnd = random.Random(7)
        for cur in ("DE", "NL"):
            d = fresh(cur)
            seq = [(max(5, rnd.gauss(50, 12)), max(5, rnd.gauss(50, 12))) for _ in range(3000)]       # 8 hours of 10 s cycles
            self.assertEqual(run(d, seq), [], f"flapped from {cur}")

    def test_heavy_jitter_with_a_real_gap_below_the_margin_does_not_flap(self):
        rnd = random.Random(11)
        d = fresh("NL")
        seq = [(max(5, rnd.gauss(55, 20)), max(5, rnd.gauss(70, 20))) for _ in range(3000)]           # DE is really 15 ms better, both are very noisy (sigma 20 ms)
        sw = run(d, seq)
        self.assertLessEqual(len(sw), 2, sw)                                                          # at most one move to the truly faster country in 8 hours, never a ping-pong
        self.assertTrue(all(s[1] == "DE" for s in sw))

    def test_a_real_step_switches_exactly_once_after_the_confirmation(self):
        d = fresh("DE")
        seq = [(50, 60)] * 20 + [(50, 25)] * 60                 # NL becomes 25 ms faster than DE and stays so
        sw = run(d, seq)
        self.assertEqual([s[1] for s in sw], ["NL"])
        # the EWMA needs a few cycles to follow the step, then 3 confirmations: not before cycle 23
        self.assertGreaterEqual(sw[0][0], 23)
        self.assertLess(sw[0][0], 40)

    def test_flapping_gap_that_never_holds_three_cycles_is_ignored(self):
        d = fresh("DE")
        seq = []
        for _ in range(100):
            seq += [(50, 20), (50, 20), (50, 90)]               # NL is much faster for 2 cycles, then clearly slower: never 3 in a row after smoothing
        self.assertEqual(run(d, seq), [])

    def test_the_dwell_time_blocks_a_quick_return(self):
        d = fresh("DE")
        sw = run(d, [(100, 30)] * 15)                           # -> NL
        self.assertEqual([s[1] for s in sw], ["NL"])
        t_after = 10_000.0 + 15 * CYCLE
        sw2 = run(d, [(30, 100)] * 25, t0=t_after)             # DE is now much better, but the last switch is younger than min_dwell_s (300 s)
        first = sw2[0][0] if sw2 else None
        self.assertTrue(sw2 and first * CYCLE + (t_after - d.last_switch) >= 0)
        self.assertGreaterEqual(sw2[0][0] * CYCLE + CYCLE * 15, 300 - CYCLE * 3)    # the return happened no earlier than ~dwell after the first switch

    def test_a_failure_overrides_the_dwell_time(self):
        d = fresh("DE")
        run(d, [(100, 30)] * 15)                                # -> NL at ~cycle 12
        sw = run(d, [(30, None)] * 4, t0=10_000.0 + 15 * CYCLE)
        self.assertEqual([s[1] for s in sw], ["DE"])            # NL died right after the switch: leave at once, dwell does not apply to failures


class TestStartAndPin(unittest.TestCase):
    def test_start_takes_the_faster_country_at_once(self):
        d = ad.Decider(["DE", "NL"], P)
        sw = run(d, [(80, 45)])
        self.assertEqual([s[1] for s in sw], ["NL"])

    def test_start_with_one_country_down_takes_the_other(self):
        d = ad.Decider(["DE", "NL"], P)
        sw = run(d, [(None, 70)])
        self.assertEqual([s[1] for s in sw], ["NL"])

    def test_start_with_nothing_answering_waits_then_blocks(self):
        d = ad.Decider(["DE", "NL"], P)
        sw = run(d, [(None, None)] * 4)
        self.assertEqual([s[1] for s in sw], [ad.NONE])
        self.assertEqual(sw[0][0], 2)

    def test_pin_is_followed_while_healthy_and_dropped_when_the_country_dies(self):
        d = fresh("DE")
        d.pin = "NL"
        sw = run(d, [(30, 90)] * 3)
        self.assertEqual([s[1] for s in sw], ["NL"])            # the operator's pin wins over latency
        sw2 = run(d, [(30, None)] * 5, t0=20_000.0)
        self.assertEqual([s[1] for s in sw2], ["DE"])           # a dead pinned country is not kept


class TestDaemonPlumbing(unittest.TestCase):
    def test_a_cycle_applies_the_choice_writes_status_and_survives_a_restart(self):
        applied = []
        lat = {20811: 40.0, 20813: 90.0}
        probe = lambda entrance, conf: lat[entrance]          # noqa: E731
        applier = lambda conf, target: applied.append(target) or True       # noqa: E731
        with tempfile.TemporaryDirectory() as td:
            clock = [50_000.0]
            d = ad.Director(dict(P), now=lambda: clock[0], probe=probe, applier=applier, state_dir=td)
            st = d.cycle()
            self.assertEqual((st["cur"], applied[-1]), ("DE", "DE"))
            self.assertEqual(st["measured_ms"], {"DE": 40.0, "NL": 90.0})
            self.assertTrue(os.path.exists(os.path.join(td, "status.json")))
            # "the controller restarts": the choice and the smoothing are remembered, there is no new switch
            d2 = ad.Director(dict(P), now=lambda: clock[0] + 10, probe=probe, applier=applier, state_dir=td)
            self.assertEqual(d2.decider.cur, "DE")
            st2 = d2.cycle()
            self.assertEqual(st2["cur"], "DE")
            self.assertEqual(len(d2.switches), 1)
            # the operator pins NL through the pin file
            open(os.path.join(td, "pin"), "w").write("NL\n")
            st3 = d2.cycle()
            self.assertEqual((st3["cur"], applied[-1], st3["pin"]), ("NL", "NL", "NL"))

    def test_both_down_pins_the_blackhole(self):
        applied = []
        with tempfile.TemporaryDirectory() as td:
            clock = [1.0]
            d = ad.Director(dict(P), now=lambda: clock[0], probe=lambda e, c: None, applier=lambda c, t: applied.append(t) or True, state_dir=td)
            for _ in range(4):
                clock[0] += 10
                d.cycle()
            self.assertEqual(applied[-1], ad.NONE)

    def test_apply_target_uses_the_blackhole_for_none_and_the_loopback_for_a_country(self):
        calls = []
        orig = (ad.xray_api, ad.current_override)
        state = {"cur": ""}
        ad.current_override = lambda conf: state["cur"]
        ad.xray_api = lambda conf, *a: (calls.append(a), state.update(cur=a[-1]), (0, ""))[2]
        try:
            self.assertTrue(ad.apply_target(P, "NL"))
            self.assertEqual(calls[-1], ("bo", "-b", "auto-director", "auto-via-s3"))
            self.assertTrue(ad.apply_target(P, ad.NONE))
            self.assertEqual(calls[-1], ("bo", "-b", "auto-director", "block"))
            n = len(calls)
            self.assertTrue(ad.apply_target(P, ad.NONE))        # already so: no second call
            self.assertEqual(len(calls), n)
            self.assertTrue(ad.apply_target(P, None))
        finally:
            ad.xray_api, ad.current_override = orig

    def test_override_parser_reads_the_xray_output(self):
        orig = ad.xray_api
        try:
            ad.xray_api = lambda conf, *a: (0, "- Selecting Override:     1   auto-via-s1      - Selects:     1   auto-via-s3")
            self.assertEqual(ad.current_override(P), "auto-via-s1")
            ad.xray_api = lambda conf, *a: (0, "- Selecting Override:     0   - Selects:     2   auto-via-s1 auto-via-s3")
            self.assertEqual(ad.current_override(P), "")
            ad.xray_api = lambda conf, *a: (0, "- Selecting Override:     1\n  auto-via-s3\n- Selects:     1\n  auto-via-s1")      # the same text on several lines
            self.assertEqual(ad.current_override(P), "auto-via-s3")
            ad.xray_api = lambda conf, *a: (0, "- Selecting Override:     0\n- Selects:     2\n  auto-via-s1\n  auto-via-s3")
            self.assertEqual(ad.current_override(P), "")
            ad.xray_api = lambda conf, *a: (1, "rpc error")
            self.assertIsNone(ad.current_override(P))
        finally:
            ad.xray_api = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)
