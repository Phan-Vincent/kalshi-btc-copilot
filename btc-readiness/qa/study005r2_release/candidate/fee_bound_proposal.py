"""PROPOSED (not adopted) per-contract taker-buy fee bound for .0001 balance precision.

Derivation for one order of Q contracts split into n fills of q_i (each a multiple
of .01, so n <= 100*Q): each fill's net fee is at most its six-decimal ceiling
trade fee (< .07*q_i*p*(1-p) + .000001) plus a rounding amount below .0001, and
rebates are nonnegative. Summing gives fee < Q * (.07*p*(1-p) + .0101).

Applies only if the account's balance precision is .0001, the KXBTC15M series
stays quadratic with multiplier 1, and there are no unreported adjustments. The
frozen studies keep their original bounds; adoption needs a separately reviewed
and newly sealed protocol.
"""
from decimal import Decimal as D

PER_FILL_SLACK = D('.000101')
FILLS_PER_CONTRACT = 100


def taker_buy_fee_bound_0001(price, multiplier='1'):
    price, multiplier = D(str(price)), D(str(multiplier))
    if not price.is_finite() or not 0 < price < 1 or price % D('.0001'):
        raise ValueError('price must be in (0, 1) on a .0001 grid')
    if multiplier != 1:
        raise ValueError('only multiplier 1 has been checked')
    return D('.07') * multiplier * price * (1 - price) + FILLS_PER_CONTRACT * PER_FILL_SLACK
