import pytest

from diamond_hands.config import load_config

CONFIG = """
timezone: America/St_Johns
base_currency: CAD
account_equity: 37615
watchlist: []
paper_book:
  enabled: true
  starting_capital: 100000
  benchmark: VFV.TO
"""


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(CONFIG)
    return load_config(p)
