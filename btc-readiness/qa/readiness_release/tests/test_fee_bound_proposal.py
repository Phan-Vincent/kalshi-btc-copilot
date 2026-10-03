import importlib.util, random, unittest
from decimal import Decimal as D
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'candidate' / (name + '.py'))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod
fb, fr = load('fee_bound_proposal'), load('fee_reconciliation')


def groupings(quantity, rng):
    """Worst case (all .01 fills), one fill, and random partitions of the quantity."""
    units = int(quantity * 100)
    yield [D('.01')] * units
    yield [quantity]
    for _ in range(20):
        left, parts = units, []
        while left:
            take = rng.randint(1, left); parts.append(D(take) / 100); left -= take
        yield parts


class FeeBoundProposalTests(unittest.TestCase):
    def test_bound_dominates_every_grouping_at_0001(self):
        rng = random.Random(20260927)
        prices = [D(c) / 100 for c in range(1, 100)] + [D('.0001'), D('.0550'), D('.9999'), D('.4321')]
        for price in prices:
            bound = fb.taker_buy_fee_bound_0001(price)
            for quantity in (D('.01'), D('1'), D('2.37')):
                for parts in groupings(quantity, rng):
                    fee = D(fr.fee_sequence([(price, q, 'buy') for q in parts], '.0001')['fee'])
                    self.assertLessEqual(fee, bound * quantity, (price, quantity, len(parts)))

    def test_bound_is_not_the_cent_precision_bound(self):
        # The same 100 x .01 path at .01 precision costs $1 total, far above this bound.
        fee = D(fr.fee_sequence([(D('.50'), D('.01'), 'buy')] * 100, '.01')['fee'])
        self.assertEqual(fee, D('.50'))
        self.assertLess(fb.taker_buy_fee_bound_0001('.50'), D('.03'))

    def test_bound_values(self):
        self.assertEqual(fb.taker_buy_fee_bound_0001('.50'), D('.0276'))
        self.assertEqual(fb.taker_buy_fee_bound_0001('.01'), D('.010793'))

    def test_rejects_out_of_scope_inputs(self):
        for price in ('0', '1', '1.5', '-.1', '.00005', 'NaN'):
            with self.assertRaises(Exception):
                fb.taker_buy_fee_bound_0001(price)
        with self.assertRaises(ValueError):
            fb.taker_buy_fee_bound_0001('.5', '2')


if __name__ == '__main__':
    unittest.main(verbosity=2)
