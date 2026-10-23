"""Offline tests for analyze.py (stdlib unittest, no network): python -m unittest test_analyze"""
import os
import unittest

os.environ.setdefault("DIPLAB_RPC", "http://127.0.0.1:0")  # chain.py reads it at import; never called here

import analyze  # noqa: E402
import chain as ch  # noqa: E402

WALLET = "0x" + "11" * 20
ROUTER = "0x65050a9b7e5075a2ba5ced7b1b64ee66262c40dc"
CURVE = "0x" + "22" * 20
E18 = 10 ** 18


def _word(n):
    return f"{n:064x}"


def _log(topic0, t1, t2, *words, tx="0x" + "aa" * 32, index="0x0"):
    return {
        "address": CURVE,
        "topics": [topic0, ch.topic_for(t1), ch.topic_for(t2)],
        "data": "0x" + "".join(_word(w) for w in words),
        "transactionHash": tx,
        "logIndex": index,
    }


class HabitCurveLogs(unittest.TestCase):
    def setUp(self):
        # Bought through the Pons router (buyer = router, recipient = wallet): 1 ETH -> 1,000 tokens.
        # Sold straight on the curve (seller = recipient = wallet): 1,000 tokens -> 1.5 ETH.
        self.logs = [
            _log(ch.CURVE_BUY, ROUTER, WALLET, 1 * E18, 1000 * E18, 0, 0, tx="0x" + "01" * 32),
            _log(ch.CURVE_SELL, WALLET, WALLET, 1000 * E18, 3 * E18 // 2, 0, 0, tx="0x" + "02" * 32),
        ]
        self._get_logs, self._tail_logs = ch.get_logs, analyze._tail_logs

        def get_logs(lo, hi, address=None, topics=None):
            out = []
            for lg in self.logs:
                tp = lg["topics"]
                if all(want is None or tp[i].lower() == want.lower() for i, want in enumerate(topics)):
                    out.append(lg)
            return out

        ch.get_logs = get_logs
        analyze._tail_logs = lambda *a, **k: []

    def tearDown(self):
        ch.get_logs, analyze._tail_logs = self._get_logs, self._tail_logs

    def test_log_matching_both_roles_counts_once(self):
        med, n, traded = analyze._habit_compute(100, WALLET, "0x" + "33" * 20)
        self.assertEqual(n, 1)
        self.assertAlmostEqual(med, 1.5)


if __name__ == "__main__":
    unittest.main()
