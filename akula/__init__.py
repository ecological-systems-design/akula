__all__ = (
    "DATA_DIR",
    # "generate_markets_datapackage",
)

from pathlib import Path

DATA_DIR = Path(__file__).parent.resolve() / "data"

from .version import version as __version__
# from .markets import generate_markets_datapackage
from .parameterization import generate_parameterization_datapackage
from .combustion import generate_combustion_datapackage
