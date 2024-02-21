from pathlib import Path
import bw2data as bd

import os
os.environ["ENTSOE_API_TOKEN"] = "0d6ea062-f603-43d3-bc60-176159803035"
os.environ["BENTSO_DATA_DIR"] = "/home/aleksandrakim/LCAfiles/bentso_data"

from akula.sensitivity_analysis import (
    get_tmask_wo_noninf,
    get_bmask_wo_noninf,
    get_cmask_wo_noninf,
    get_masks_wo_lowinf,
    run_mc_simulations_all_inputs,
    run_mc_simulations_wo_noninf,
    run_mc_simulations_wo_lowinf,
)
from akula.utils import compute_deterministic_score
from akula.monte_carlo import plot_lcia_scores_from_two_cases

PROJECT = "GSA with correlations"
PROJECT_EXIOBASE = "GSA with correlations, exiobase"

PROJECT_DIR = Path(__file__).parent.parent.resolve()
FIGURES_DIR = PROJECT_DIR / "figures"
FP_ECOINVENT = "/home/aleksandrakim/LCAfiles/ecoinvent_38_cutoff/datasets"

# Parameters for GSA
SEED = 222201
ITERATIONS_VALIDATION = 50
CUTOFF = 1e-7
MAX_CALC = 1e18
FACTOR = 10
NUM_LOWINF = 10_000


if __name__ == "__main__":

    # =========================================================================
    # 0. Some setups
    # =========================================================================
    # 0.1 Compute LCIA score offset when exiobase is used
    # exiobase_lcia = compute_deterministic_score(PROJECT_EXIOBASE)
    # no_exiobase_lcia = compute_deterministic_score(PROJECT)
    # exiobase_offset = exiobase_lcia - no_exiobase_lcia
    exiobase_offset = 703.1540208909953
    # 0.2 Run Monte Carlo simulations when all TECH, BIO and CF inputs vary, including 4 sampling modules
    scores_all = run_mc_simulations_all_inputs(PROJECT, FP_ECOINVENT, ITERATIONS_VALIDATION, SEED)

    # =========================================================================
    # 1. Remove NON-influential inputs
    # =========================================================================
    tmask_wo_noninf = get_tmask_wo_noninf(PROJECT, CUTOFF, MAX_CALC)  # takes ~25 min for cutoff=1e-7, max_calc=1e18
    bmask_wo_noninf = get_bmask_wo_noninf(PROJECT)
    cmask_wo_noninf = get_cmask_wo_noninf(PROJECT)

    print(f"{sum(tmask_wo_noninf):6d} / {len(tmask_wo_noninf):6d} TECH inputs after removing NON influential "
                                                                       f"with Supply Chain Traversal")
    print(f"{sum(bmask_wo_noninf):6d} / {len(bmask_wo_noninf):6d}  BIO inputs after removing NON influential "
                                                                       f"with Biosphere Matrix Analysis")
    print(f"{sum(cmask_wo_noninf):6d} / {len(cmask_wo_noninf):6d}   CF inputs after removing NON influential "
                                                                       f"with Characterization Matrix Analysis\n")

    # Validate results
    ##################
    scores_wo_noninf = run_mc_simulations_wo_noninf(
        PROJECT, FP_ECOINVENT, CUTOFF, MAX_CALC, ITERATIONS_VALIDATION, SEED
    )
    figure = plot_lcia_scores_from_two_cases(scores_all, scores_wo_noninf, exiobase_offset)
    figure.write_image(FIGURES_DIR / "validation_noninf.pdf")

    # =========================================================================
    # 2.1 Remove LOWLY influential inputs with local sensitivity analysis
    # =========================================================================
    # LSA takes 14h for technosphere, 15 min for biosphere, and seconds for characterization
    # Tweak NUM_LOWINF to get the desired number of inputs to be removed based on validation results
    tmask_wo_lowinf, bmask_wo_lowinf, cmask_wo_lowinf = get_masks_wo_lowinf(
        PROJECT, FACTOR, CUTOFF, MAX_CALC, NUM_LOWINF
    )

    print(f"{sum(tmask_wo_lowinf):6d} / {len(tmask_wo_lowinf):6d} TECH inputs after removing LOWLY influential "
                                                                       f"with Local Sensitivity Analysis")
    print(f"{sum(bmask_wo_lowinf):6d} / {len(bmask_wo_lowinf):6d}  BIO inputs after removing LOWLY influential "
                                                                       f"with Local Sensitivity Analysis")
    print(f"{sum(cmask_wo_lowinf):6d} / {len(cmask_wo_lowinf):6d}   CF inputs after removing LOWLY influential "
                                                                       f"with Local Sensitivity Analysis\n")

    # Validate results
    ##################
    scores_wo_lowinf = run_mc_simulations_wo_lowinf(
        PROJECT, FP_ECOINVENT, FACTOR, CUTOFF, MAX_CALC, ITERATIONS_VALIDATION, SEED
    )
    figure = plot_lcia_scores_from_two_cases(scores_all, scores_wo_lowinf, exiobase_offset)
    figure.write_image(FIGURES_DIR / "validation_lowinf.pdf")


    # =========================================================================
    # 3. Check model linearity
    # =========================================================================

    # =========================================================================
    # 4. Factor fixing with XGBoost
    # =========================================================================

    # =========================================================================
    # 5. Factor prioritization with Shapley values
    # =========================================================================
