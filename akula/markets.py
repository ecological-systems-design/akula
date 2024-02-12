from collections import defaultdict
from pathlib import Path
from copy import deepcopy
import bw2data as bd
import bw_processing as bwp
import numpy as np
import stats_arrays as sa
from fs.zipfs import ZipFS
from scipy.stats import dirichlet, lognorm
from thefuzz import fuzz
from tqdm import tqdm
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .utils import (
    read_pickle, write_pickle,
    update_fig_axes, COLOR_DARKGRAY_HEX, COLOR_PSI_LPURPLE, COLOR_PSI_DGREEN,
)

DATA_DIR = Path(__file__).parent.parent.resolve() / "data" / "datapackages"


def similar_fuzzy(a, b):
    """Fuzzy comparison between `a` and `b` strings using partial ratio."""
    return fuzz.partial_ratio(a, b) > 90 or fuzz.ratio(a, b) > 40


def find_markets(database, similarity_func):
    """Find markets based on similar reference product names based on exact or fuzzy string comparison."""

    db = bd.Database(database)

    found = {}

    for act in tqdm(db):
        rp = act.get("reference product")
        if not rp:
            continue

        inpts = defaultdict(list)
        for exc in act.technosphere():
            if exc.input == exc.output:
                continue
            elif exc.get("uncertainty type", 0) < 2:
                continue
            elif exc.input.get("reference product", None) is None:
                continue
            inpts[exc.input["reference product"]].append(exc)

        for key, lst in inpts.items():
            if (
                len(lst) > 1
                and similarity_func(rp, key)
                and 0.98 <= sum([exc["amount"] for exc in lst]) <= 1.02
            ):
                found[act] = lst

        # print(rp, len(found))

    return found


def find_entsoe_markets(similarity_func):
    dp = bwp.load_datapackage(ZipFS(str(DATA_DIR / "entsoe-timeseries.zip")))

    indices = dp.get_resource('timeseries ENTSO electricity values.indices')[0]
    data = dp.get_resource('timeseries ENTSO electricity values.data')[0]

    # Fit lognormal distributions to ENTSO-E timeseries data
    distributions = fit_distributions(data, indices)
    distributions_dict = {tuple(i): d for i, d in zip(indices, distributions)}

    found = {}
    unique_cols = sorted(list(set(indices['col'])))
    for col in unique_cols:

        act = bd.get_activity(col)

        rp = act.get("reference product")
        if not rp:
            continue

        rows = sorted(indices[indices['col'] == col]['row'])

        inpts = defaultdict(list)
        for exc in act.technosphere():

            if exc.input.id in rows:

                exc_dict = deepcopy(exc.as_dict())
                params = distributions_dict[(exc.input.id, col)]
                distribution = {p[0].replace("_", " "): params[p[0]] for p in bwp.UNCERTAINTY_DTYPE}
                exc_dict.update(**distribution)

                if exc_dict["uncertainty type"] == 2:
                    if exc.input['name'] == "swiss residual electricity mix":
                        inpts["swiss residual electricity mix"].append(exc_dict)
                    else:
                        inpts[exc.input["reference product"]].append(exc_dict)

        for key, lst in inpts.items():
            if (
                len(lst) > 1
                and similarity_func(rp, key)
                and 0.98 <= sum([exc["amount"] for exc in lst]) <= 1.02
            ):
                found[act] = lst

    return found


def get_beta_variance(a, b):
    """Compute variance of beta distribution based on shape parameters `a` and `b`."""
    return a*b/(a+b)**2/(a+b+1)


def get_lognormal_variance(loc, scale):
    """Compute variance of lognormal distribution based on parameters `loc` and `scale`."""
    return (np.exp(scale**2)-1) * np.exp(2*loc+scale**2)


def select_higher_amount_exchanges(amounts_exchanges):
    """Select exchanges in the given market that have amounts higher than average."""

    alphas = list(amounts_exchanges)
    threshold = np.mean(alphas)

    exchanges = {}

    for amount, exc in amounts_exchanges.items():
        if amount >= threshold:
            if exc['uncertainty type'] == 2:
                exchanges[amount] = exc

    return exchanges


def get_dirichlet_scale(amounts_exchanges):
    """Compute dirichlet scale for exchanges, where the Dirichlet parameter `alpha` is set to exchange amounts."""
    alphas = list(amounts_exchanges)
    beta = sum(alphas)

    scaling_factors = []
    selected_exchanges = select_higher_amount_exchanges(amounts_exchanges)

    for ialpha, iexc in selected_exchanges.items():
        loc = iexc['loc']
        scale = iexc['scale']
        if iexc['uncertainty type'] == 2:
            beta_variance = get_beta_variance(ialpha, beta-ialpha)
            lognormal_variance = get_lognormal_variance(loc, scale)
            if lognormal_variance:
                scaling_factors.append(beta_variance / lognormal_variance * 2)

    scaling_factor = np.mean(scaling_factors)

    return scaling_factor


def get_dirichlet_scales(markets):
    """Get Dirichlet scales for all implicit markets.

    TODO
    This code is not good because it assumes that the file dirichlet_scales.pickle exists for validation steps.
    If this file does not exist, some scales might be equal to zero which is incorrect.

    """

    dirichlet_scales = {}

    for market, exchanges in markets.items():
        x = np.array([exc['amount'] for exc in exchanges])
        amounts = x.copy()
        amounts_exchanges_dict = {amounts[i]: exchanges[i] for i in range(len(amounts))}
        ds = get_dirichlet_scale(amounts_exchanges_dict)
        dirichlet_scales[market] = ds

    return dirichlet_scales


def generate_markets_datapackage(name, num_samples, seed=42, for_entsoe=False):
    fp_datapackage = DATA_DIR / f"{name}-{seed}-{num_samples}.zip"

    if not fp_datapackage.exists():

        fp_markets = DATA_DIR / f"{name}.pickle"
        if fp_markets.exists():
            markets = read_pickle(fp_markets)
        else:
            if for_entsoe:
                markets = find_entsoe_markets(similar_fuzzy)
            else:
                markets = find_markets("ecoinvent 3.8 cutoff", similar_fuzzy)
            write_pickle(markets, fp_markets)

        data, indices, flip = generate_market_samples(markets, num_samples, seed=seed)

        dp = bwp.create_datapackage(
            fs=ZipFS(str(fp_datapackage), write=True),
            name=name,
            seed=seed,
            sequential=True,
        )

        dp.add_persistent_array(
            matrix="technosphere_matrix",
            data_array=data,
            # Resource group name that will show up in provenance
            name=name,
            indices_array=indices,
            flip_array=flip,
        )

        dp.finalize_serialization()

    else:

        dp = bwp.load_datapackage(ZipFS(str(fp_datapackage)))

    return dp


def check_dirichlet_samples(markets, indices, data_array):
    for market, exchanges in markets.items():
        col = market.id
        where = []
        for exc in exchanges:
            try:
                where.append(np.where(indices == np.array((exc.input.id, col), dtype=bwp.INDICES_DTYPE))[0][0])
            except AttributeError:
                where.append(np.where(indices == np.array((bd.get_activity(exc["input"]).id, col),
                                                          dtype=bwp.INDICES_DTYPE))[0][0])
        where = np.array(where)
        sum_ = data_array[where].sum(axis=0)
        assert np.allclose(min(sum_), max(sum_))


def generate_market_samples(markets, num_samples, seed=42):

    try:
        indices_array = np.array(
            [(exc.input.id, exc.output.id) for lst in markets.values() for exc in lst],
            dtype=bwp.INDICES_DTYPE,
        )
    except AttributeError:
        indices_array = np.array(
            [(bd.get_activity(exc["input"]).id, bd.get_activity(exc["output"]).id)
             for lst in markets.values() for exc in lst],
            dtype=bwp.INDICES_DTYPE,
        )

    dirichlet_scales = get_dirichlet_scales(markets)

    data = []
    flip = []

    # Dirichlet samples of different exchanges from one market should be generated with the same seed
    np.random.seed(seed)
    seeds = {market: np.random.randint(0, 2**32-1) for market in markets}

    for inds in indices_array:

        market = bd.get_activity(int(inds['col']))
        exchanges = markets[market]
        try:
            where_exc = [i for i in range(len(exchanges)) if exchanges[i].input.id == inds['row']][0]
        except AttributeError:
            where_exc = [
                i for i in range(len(exchanges)) if bd.get_activity(exchanges[i]["input"]).id == inds['row']
            ][0]
        selected_exchanges = markets.get(market, [])

        if len(selected_exchanges) > 1 and inds in indices_array:

            total_amount = sum([exc['amount'] for exc in selected_exchanges])
            try:
                where_selected_exc = [
                    i for i in range(len(selected_exchanges)) if selected_exchanges[i].input.id == inds['row']
                ][0]
            except AttributeError:
                where_selected_exc = [
                    i for i in range(len(selected_exchanges))
                    if bd.get_activity(selected_exchanges[i]["input"]).id == inds['row']
                ][0]
            np.random.seed(seeds[market])
            samples = dirichlet.rvs(
                np.array([exc["amount"] for exc in selected_exchanges]) * dirichlet_scales[market],
                size=num_samples,
                ) * total_amount
            data.append(samples[:, where_selected_exc])
            flip.append(selected_exchanges[where_selected_exc]['type'] != "production")

        else:

            data.append(np.ones(num_samples) * exchanges[where_exc]['amount'])
            flip.append(exchanges[where_exc]["type"] != "production")

    data_array = np.vstack(data)
    flip_array = np.array(flip, dtype=bool)

    # Sanity check to ensure that samples in each market sum up to 1
    check_dirichlet_samples(markets, indices_array, data_array)

    return data_array, indices_array, flip_array


def get_distributions(indices):

    ddict = dict()
    assert bwp.UNCERTAINTY_DTYPE[-1][0] == "negative"

    cols = sorted(set(indices['col']))
    for col in cols:
        rows = sorted(indices[indices['col'] == col]['row'])
        act = bd.get_activity(int(col))
        for exc in act.exchanges():
            if exc.input.id in rows:
                ddict[(exc.input.id, col)] = (
                    [exc.get(p[0].replace("_", " "), np.nan) for p in bwp.UNCERTAINTY_DTYPE[:-1]]
                    + [exc["type"] == "production"])

    dlist = [tuple(ddict[row, col]) for row, col in indices]
    distributions = np.array(dlist, dtype=bwp.UNCERTAINTY_DTYPE)

    return distributions


def plot_dirichlet_samples(act_id, dp_markets, num_bins=100):

    data = dp_markets.get_resource('markets.data')[0]
    indices = dp_markets.get_resource('markets.indices')[0]
    mask = indices['col'] == act_id
    data = data[mask, :]
    indices = indices[mask]

    num_exchanges = len(indices)

    distributions = get_distributions(indices)
    assert np.all(distributions["uncertainty_type"] == sa.LognormalUncertainty.id)

    fig = make_subplots(rows=num_exchanges, cols=1, subplot_titles=["placeholder"]*num_exchanges)
    opacity = 0.65
    showlegend = True

    for i in range(num_exchanges):

        row = indices[i]['row']
        act = bd.get_activity(row)
        fig.layout.annotations[i]['text'] = f"{act['name'][:60]} -- {act['location']}"

        loc = distributions[i]['loc']
        scale = distributions[i]['scale']
        min_distr = lognorm.ppf(0.001, s=scale, scale=np.exp(loc))
        max_distr = lognorm.ppf(0.999, s=scale, scale=np.exp(loc))

        Y = data[i, :]
        min_samples = min(Y)
        max_samples = max(Y)

        bin_min = min(min_distr, min_samples)
        bin_max = max(max_distr, max_samples)

        bins_ = np.linspace(bin_min, bin_max, num_bins + 1, endpoint=True)
        Y_samples, _ = np.histogram(Y, bins=bins_, density=True)

        midbins = (bins_[1:] + bins_[:-1]) / 2
        Y_distr = lognorm.pdf(midbins, s=scale, scale=np.exp(loc))

        # Plot Dirichlet samples
        fig.add_trace(
            go.Scatter(
                x=midbins,
                y=Y_samples,
                name=r"$\text{Dirichlet samples}$",
                showlegend=showlegend,
                opacity=opacity,
                line=dict(color=COLOR_DARKGRAY_HEX, width=1, shape="hvh"),
                fill="tozeroy",
            ),
            row=i+1,
            col=1,
        )
        # Plot lognormal distribution
        fig.add_trace(
            go.Scatter(
                x=midbins,
                y=Y_distr,
                line=dict(color=COLOR_PSI_LPURPLE),
                name=r"$\text{Defined lognormal}$",
                showlegend=showlegend,
                legendrank=1,
            ),
            row=i+1,
            col=1,
        )
        showlegend = False
    #
    fig.update_xaxes(title_text=r"$\text{Production volume share}$")
    fig.update_yaxes(title_text=r"$\text{Frequency}$")
    fig = update_fig_axes(fig)
    if num_exchanges < 10:
        offset = 0.1
    else:
        offset = 0.005
    fig.update_layout(
        width=600, height=180*num_exchanges + 40,
        legend=dict(yanchor="bottom", y=1 + offset, xanchor="center", x=0.5,
                    orientation='h', font=dict(size=13)),
        margin=dict(t=10, b=10, l=10, r=10),
    )

    return fig


def fit_distributions(data, indices):

    dlist = []
    assert bwp.UNCERTAINTY_DTYPE[0][0] == "uncertainty_type"
    assert bwp.UNCERTAINTY_DTYPE[-1][0] == "negative"

    for i, d in enumerate(data):
        dpos = d[d > 0]
        if len(dpos) and (len(set(d)) > 1):
            shape, loc, scale = lognorm.fit(dpos, floc=0)
            params = dict(uncertainty_type=sa.LognormalUncertainty.id, loc=np.log(scale), scale=shape)
        else:
            params = dict(uncertainty_type=sa.NoUncertainty.id)
        distribution = ([params.get(p[0], np.nan) for p in bwp.UNCERTAINTY_DTYPE[:-1]]
                        + [indices[i]["row"] == indices[i]["col"]])
        dlist.append(tuple(distribution))

    distributions = np.array(dlist, dtype=bwp.UNCERTAINTY_DTYPE)

    return distributions


def plot_dirichlet_entsoe_samples(act_id, dp_entsoe, dp_dirichlet, num_bins=100):

    # Get Dirichlet distributions samples that were fit to ENTSO-E data
    indices = dp_dirichlet.get_resource('markets-entsoe.indices')[0]
    mask = indices['col'] == act_id
    num_exchanges = int(sum(mask))

    indices_act = indices[mask]
    data_dirichlet = dp_dirichlet.get_resource('markets-entsoe.data')[0]
    data_dirichlet = data_dirichlet[mask, :]

    # Extract ENTSO-E data for the given activity
    indices = dp_entsoe.get_resource('entsoe.indices')[0]
    where = []
    for inds in indices_act:
        where.append(np.where(indices == inds)[0][0])
    data_entsoe = dp_entsoe.get_resource('entsoe.data')[0]
    data_entsoe = data_entsoe[where, :]

    # Fit lognormal distributions to ENTSO-E data
    distributions_lognorm = fit_distributions(data_entsoe, indices)

    fig = make_subplots(rows=num_exchanges, cols=1, subplot_titles=["placeholder"]*num_exchanges)
    opacity = 0.65
    showlegend = True

    for i in range(num_exchanges):

        row = indices[i]['row']
        act = bd.get_activity(row)

        fig.layout.annotations[i]['text'] = f"{act['name'][:60]} -- {act['location']}"

        Y_entsoe = data_entsoe[i, :]
        min_entsoe = min(Y_entsoe)
        max_entsoe = max(Y_entsoe)

        loc = distributions_lognorm[i]['loc']
        scale = distributions_lognorm[i]['scale']
        min_lognorm = lognorm.ppf(0.001, s=scale, scale=np.exp(loc))
        max_lognorm = lognorm.ppf(0.999, s=scale, scale=np.exp(loc))

        Y_dirichlet = data_dirichlet[i, :]
        min_dirichlet = min(Y_dirichlet)
        max_dirichlet = max(Y_dirichlet)

        bin_min = max(min(min_lognorm, min_entsoe, min_dirichlet), 0)
        bin_max = min(max(max_lognorm, max_entsoe, max_dirichlet), 1)

        range_min = min(min_entsoe, min_dirichlet)
        range_max = max(max_entsoe, max_dirichlet)

        bins_ = np.linspace(bin_min, bin_max, num_bins + 1, endpoint=True)
        midbins = (bins_[1:] + bins_[:-1]) / 2
        Y_entsoe, _ = np.histogram(Y_entsoe, bins=bins_, density=True)
        Y_lognorm = lognorm.pdf(midbins, s=scale, scale=np.exp(loc))
        Y_dirichlet, _ = np.histogram(Y_dirichlet, bins=bins_, density=True)

        # Plot ENTSO-E samples
        fig.add_trace(
            go.Scatter(
                x=midbins,
                y=Y_entsoe,
                name=r"$\text{ENTSO-E samples}$",
                showlegend=showlegend,
                opacity=opacity,
                line=dict(color=COLOR_DARKGRAY_HEX, width=1, shape="hvh"),
                fill="tozeroy",
            ),
            row=i+1,
            col=1,
        )
        # Plot fitted lognormal distribution
        fig.add_trace(
            go.Scatter(
                x=midbins,
                y=Y_lognorm,
                line=dict(color="red"),
                name=r"$\text{Fitted lognormal}$",
                showlegend=showlegend,
                legendrank=1,
            ),
            row=i+1,
            col=1,
        )
        # Plot Dirichlet samples
        fig.add_trace(
            go.Scatter(
                x=midbins,
                y=Y_dirichlet,
                name=r"$\text{Dirichlet samples}$",
                showlegend=showlegend,
                opacity=opacity,
                line=dict(color=COLOR_PSI_DGREEN, width=1, shape="hvh"),
                fill="tozeroy",
            ),
            row=i+1,
            col=1,
        )
        fig.update_xaxes(range=[range_min, range_max], row=i+1, col=1)
        showlegend = False

    fig.update_xaxes(title_text=r"$\text{Production volume share}$")
    fig.update_yaxes(title_text=r"$\text{Frequency}$")
    fig = update_fig_axes(fig)
    if num_exchanges < 10:
        offset = 0.1
    else:
        offset = 0.005
    fig.update_layout(
        width=600, height=180*num_exchanges + 40,
        legend=dict(yanchor="bottom", y=1 + offset, xanchor="center", x=0.5,
                    orientation='h', font=dict(size=13)),
        margin=dict(t=10, b=10, l=10, r=10),
    )

    return fig
