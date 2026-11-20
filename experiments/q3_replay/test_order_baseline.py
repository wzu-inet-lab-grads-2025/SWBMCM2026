import unittest

from .data import configuration
from .order_baseline import order_policy, synthetic_log


def job(name, *, release=0, due=10, family="P", zone="Z0", predecessors=()):
    return dict(
        job_id=name,
        family_id=family,
        release_s=release,
        due_s=due,
        zone_id=zone,
        predecessors=predecessors,
    )


def public_view(*jobs, t=0, budget=32, horizon=100, done=()):
    rules, zones = configuration()
    return dict(
        time=t,
        budget_left=budget,
        zone="Z0",
        zones=zones,
        case={"horizon_s": horizon},
        jobs={j["job_id"]: j for j in jobs},
        completed={j: 0 for j in done},
        alpha=1,
        beta=0.5,
        rules={
            "fees": rules["fees"],
            "movement": rules["movement"],
            "reserve_min_s": rules["service"]["reserve_min_s"],
        },
    )


class OrderBaselineTests(unittest.TestCase):
    def test_admission_filters_before_ranking(self):
        # D moves from Z0 to Z4, too costly. Long reservation for P will not fit.
        candidates = [
            job("A_future", release=4),
            job("B_done"),
            job("C_blocked", predecessors=["A_future"]),
            job("D_far", zone="Z4", family="A"),
            job("E_long"),
            job("F_feasible", family="A"),
        ]
        view = public_view(*candidates, budget=2.4, horizon=4.5, done=["B_done"])
        for order in ("FCFS", "EDD"):
            self.assertEqual(
                order_policy(order)(view),
                {"action": "EXECUTE", "job_id": "F_feasible", "b_s": 3.7},
            )

    def test_equal_primary_key_orders_by_id_and_includes_exact_bounds(self):
        view = public_view(job("later_id"), job("earlier_id"), horizon=5, budget=2.45)
        for order in ("FCFS", "EDD"):
            self.assertEqual(order_policy(order)(view)["job_id"], "earlier_id")

    def test_wait_and_stop_share_the_release_boundary(self):
        view = public_view(
            job("blocked", predecessors=["future"]),
            job("future", release=2),
            job("at_H", release=10),
            horizon=10,
        )
        for order in ("FCFS", "EDD"):
            decide = order_policy(order)
            self.assertEqual(decide(view), {"action": "WAIT", "seconds": 2})
            self.assertEqual(
                decide(dict(view, time=2, budget_left=0)), {"action": "STOP"}
            )

    def test_source_seconds_mapped_before_reserving(self):
        view = public_view(job("L", family="L"))
        view.update(alpha=0.75, beta=0.5)
        self.assertAlmostEqual(order_policy("FCFS")(view)["b_s"], 3.2)

    def test_fee_and_changeover_can_each_exclude_a_job(self):
        decide = order_policy("FCFS")
        # At Z4 the quote for P is 2.85 cost units; time alone allows execution.
        self.assertEqual(
            decide(public_view(job("A", zone="Z4"), budget=2.84)), {"action": "STOP"}
        )
        # Same zone still needs a 0.5 second changeover before the 4.5 reservation.
        self.assertEqual(decide(public_view(job("A"), horizon=4.9)), {"action": "STOP"})

    def test_only_ranking_changes_in_synthetic_replay(self):
        pair = synthetic_log()

        def actions(order):
            return [
                (e["job_id"], e["b"])
                for e in pair[order]["ledger"]
                if e["action"] == "EXECUTE"
            ]

        self.assertEqual(actions("FCFS"), [("A", 4.5), ("B", 3.7)])
        self.assertEqual(actions("EDD"), [("B", 3.7), ("A", 4.5)])
        for order, tardy in [("FCFS", 4.0), ("EDD", 0.0)]:
            self.assertEqual(pair[order]["metrics"]["weighted_tardiness_s"], tardy)
            self.assertAlmostEqual(pair[order]["metrics"]["cost"], 5.02)
