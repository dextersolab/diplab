"""Unit tests for the V4 pool helpers in analyze.py (stdlib only, no network).

Run: python -m unittest test_analyze
"""
import math
import os
import unittest
from unittest import mock

os.environ.setdefault("DIPLAB_RPC", "http://127.0.0.1:0")  # chain.py reads it at import; never called here
import analyze  # noqa: E402

Q96 = 2 ** 96


def cp_dip(sold, reserve):
    """Constant-product price drop for selling `sold` into `reserve` (the server's formula)."""
    return 1 - (1 / (1 + sold / reserve)) ** 2


class DipMath(unittest.TestCase):
    def test_matches_constant_product_both_sides(self):
        token_reserve, other_reserve = 10 ** 27, 10 ** 20
        L = math.isqrt(token_reserve * other_reserve)
        for sold in (10 ** 24, 10 ** 26, 5 * 10 ** 26):
            want = cp_dip(sold, token_reserve)
            # token is currency0: P = other/token
            sp0 = math.isqrt(other_reserve * Q96 * Q96 // token_reserve)
            self.assertAlmostEqual(analyze._dip(sp0, L, True, sold), want, places=6)
            # token is currency1: P = token/other
            sp1 = math.isqrt(token_reserve * Q96 * Q96 // other_reserve)
            self.assertAlmostEqual(analyze._dip(sp1, L, False, sold), want, places=6)

    def test_real_pons_v2_sells(self):
        # Swap events of Pons V2 hook pools on Robinhood Chain (PoolManager 0x8366…0951):
        # sqrtPriceX96 of the previous swap, the pool liquidity, the amount the seller paid in
        # (LP fee 0) and the sqrtPriceX96 the sell left behind.
        cases = [
            # token is currency1, paired with native ETH; tx 0x5db6542d…16c1, block 89386660
            (False, 542230269603722995142990364782199, 29277002188455995981109,
             9071350709235965868912452, 566778768237186981784869774593558),
            # token is currency1, paired with USDG; tx 0x15c7ac1e…9754, block 93188148
            (False, 12707233479966739043097174831762871519, 1284920414932913417,
             8265862551373719486802357, 13216906370155039858844497465331265750),
            # token is currency0, paired with USDG; tx 0x935bc385…0d4, block 92784947
            (True, 521532605760002216980, 1284920394205829446,
             5836461236654280564286601, 506391361918165079567),
        ]
        for token_is_0, before, L, sold, after in cases:
            ratio = (after / before) ** 2
            actual = 1 - (ratio if token_is_0 else 1 / ratio)
            self.assertGreater(actual, 0.05)
            self.assertAlmostEqual(analyze._dip(before, L, token_is_0, sold), actual, places=6)


class PoolSide(unittest.TestCase):
    TOKEN = "0x03fa6a700e2a34f2f2acdd9a868e4f42c76f04a7"  # sorts below WETH, but is currency1 (pair: native ETH)
    TX = "0x5db6542d2ec63f20a35b2b69e40ef66a50480def2450c43134c2920b581216c1"
    TRANSFER = {
        "address": TOKEN, "transactionHash": TX, "blockNumber": "0x553eea4",
        "topics": ["0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
                   "0x000000000000000000000000d7a0b3a42051e8a9c5948c97238964cda64a5fcf",
                   "0x0000000000000000000000008366a39cc670b4001a1121b8f6a443a643e40951"],
        "data": "0x0000000000000000000000000000000000000000000780eee845e634b1a42f44",
    }
    SWAP = {
        "address": "0x8366a39cc670b4001a1121b8f6a443a643e40951", "transactionHash": TX,
        "topics": ["0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f",
                   "0x21b58fd5f546b7a2c546b114e413cd662cdc8478a07fb648d88756431f2b1646",
                   "0x00000000000000000000000065050a9b7e5075a2ba5ced7b1b64ee66262c40dc"],
        "data": "0x"
                "00000000000000000000000000000000000000000000000002924137567dc8f8"
                "fffffffffffffffffffffffffffffffffffffffffff87f1117ba19cb4e5bd0bc"
                "0000000000000000000000000000000000001bf1c0f8b146d12d24bf718c8e16"
                "0000000000000000000000000000000000000000000006331c062631f5933d35"
                "000000000000000000000000000000000000000000000000000000000002b56c"
                "0000000000000000000000000000000000000000000000000000000000000000",
    }

    def test_side_read_from_the_swap_not_from_weth_order(self):
        receipt = {"transactionHash": self.TX, "logs": [self.TRANSFER, self.SWAP]}
        with mock.patch.object(analyze.ch, "get_logs", return_value=[self.TRANSFER]), \
                mock.patch.object(analyze.ch, "rpc", return_value=receipt):
            sqrtP, L, token_is_0 = analyze._pool_state(1000, self.TOKEN, 0)
        self.assertFalse(token_is_0)
        self.assertEqual(sqrtP, 566778768237186981784869774593558)
        self.assertEqual(L, 29277002188455995981109)


if __name__ == "__main__":
    unittest.main()
