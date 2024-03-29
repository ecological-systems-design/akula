from pathlib import Path
import sys

import os
os.environ["ENTSOE_API_TOKEN"] = "0d6ea062-f603-43d3-bc60-176159803035"
os.environ["BENTSO_DATA_DIR"] = "/home/aleksandrakim/LCAfiles/bentso_data"

PROJECT = "GSA with correlations"
PROJECT_EXIOBASE = "GSA with correlations, exiobase"
PROJECT_DIR = Path(__file__).parent.parent.resolve()
sys.path.append(str(PROJECT_DIR))

from akula.electricity import (
    create_timeseries_entso_datapackages,
    create_average_entso_datapackages,
    add_swiss_residual_mix,
    replace_ei_with_entso,
)


if __name__ == "__main__":
    use_exiobase = False
    project = PROJECT_EXIOBASE if use_exiobase else PROJECT

    add_swiss_residual_mix(project)

    create = False
    if create:
        create_average_entso_datapackages(project)
        create_timeseries_entso_datapackages(project)

    replace = False
    if replace:
        replace_ei_with_entso(project)
