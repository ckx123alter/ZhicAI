"""Recommendation limits by product liquidity; account warning line stays separate."""
from .finance import money


def investment_limit(projection, products):
    daily = bool(products) and all(p.get('days') == 0 and p.get('redeemable') is True for p in products)
    deducted = 0 if daily else projection['reserve']
    return {**projection, 'cap': float(max(0, money(projection['minimum']) - money(deducted))),
            'deducted_reserve': deducted, 'liquidity_policy': 'daily_redeemable' if daily else 'buffered',
            'product_ids': [p['id'] for p in products]}
