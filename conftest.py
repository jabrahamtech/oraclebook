"""Ensure the package root is importable when running `pytest` directly (not just
`python -m pytest`), so `import oraclebook` resolves in CI without an editable install."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
