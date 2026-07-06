"""Strategies — subclass BaseStrategy and implement the event handlers you need.

Ships with the reference example `CompleteSetMaker` (and the analysis of why naive
complete-set market-making loses). Your own strategies live here too.
"""
from oraclebook.strategies.base import BaseStrategy
from oraclebook.strategies.complete_set_maker import CompleteSetMaker

__all__ = ["BaseStrategy", "CompleteSetMaker"]
