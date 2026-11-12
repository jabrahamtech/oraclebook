"""Strategies — subclass BaseStrategy and implement the event handlers you need.

Ships with the reference example `CompleteSetMaker` (and the analysis of why naive
complete-set market-making loses) and `LongshotFader`, a time-aware maker whose fitted edge
did not hold out of sample. Your own strategies live here too.
"""
from oraclebook.strategies.base import BaseStrategy
from oraclebook.strategies.complete_set_maker import CompleteSetMaker
from oraclebook.strategies.longshot_fader import LongshotFader

__all__ = ["BaseStrategy", "CompleteSetMaker", "LongshotFader"]
