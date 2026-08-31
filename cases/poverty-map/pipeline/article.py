"""Build the review article's data layer from the case's own published output.

Every number the article prints comes from here — data/stats.json (what the case
publishes), data/model_stats.json (what the model recorded) and data/review.json
(the independent recomputation and the four extra tests in review.py). The prose
therefore cannot drift from the pipeline.

The one block that is not computed here is LITERATURE: figures transcribed by hand
from the published papers, each with its DOI, its target variable and its validation
design, because comparing like with like is the whole point of §2.

    uv run python pipeline/article.py            # -> web/src/data/article.json
"""

from __future__ import annotations

import json
import sys

import config

OUT = config.CASE_DIR / "web" / "src" / "data" / "article.json"

# ─────────────────────────────────────────────────────────────────────────────────
# Published benchmarks, transcribed from the papers. `design` is the field that
# decides whether a number is comparable to a leave-one-province-out number:
#   insample  — index correlated with ground truth on the units it was built for
#   randomcv  — random k-fold or hold-out that does not respect space
#   spatial   — a spatially or geographically held-out estimate
# `target` distinguishes an asset/wealth index from a monetary poverty measure.
# ─────────────────────────────────────────────────────────────────────────────────
LITERATURE = [
    {"key": "putri", "short": "Putri et al. 2022",
     "cite": "Putri, Wijayanto & Sakti (2022), ISPRS Int. J. Geo-Inf. 11(5):275",
     "doi": "10.3390/ijgi11050275",
     "target": "monetary", "target_label": "BPS poverty rate (P0)",
     "unit": "38 regencies, East Java", "n": 38, "design": "insample",
     "r2": 0.50, "spearman": 0.77, "pearson": 0.71, "rmse_pp": 3.18,
     "note": "One province, so the nominal poverty line is nearly constant across the sample. "
             "A constructed index correlated with the official rates, with no hold-out."},
    {"key": "sartirano", "short": "Sartirano et al. 2023",
     "cite": "Sartirano, Kalimeri, Cattuto, Delamónica, García-Herranz, Mockler, Paolotti & "
             "Schifanella (2023), Frontiers in Big Data 6:1054156",
     "doi": "10.3389/fdata.2023.1054156",
     "target": "asset", "target_label": "Susenas asset index",
     "unit": "513 regencies, Indonesia", "n": 513, "design": "insample",
     "r2": None, "spearman": 0.75, "pearson": None, "rmse_pp": None,
     "auroc_adm2": 0.79, "auroc_adm1": 0.72, "exclusion_error": 0.3282,
     "untargeted_share": 0.5566,
     "note": "Meta's Relative Wealth Index ranked against a Filmer–Pritchett Susenas asset "
             "index at kabupaten level."},
    {"key": "jean", "short": "Jean et al. 2016",
     "cite": "Jean, Burke, Xie, Davis, Lobell & Ermon (2016), Science 353(6301):790–794",
     "doi": "10.1126/science.aaf7894",
     "target": "asset", "target_label": "DHS asset index",
     "unit": "survey clusters, 5 African countries", "n": None, "design": "randomcv",
     "r2": 0.56, "lo": 0.55, "hi": 0.75, "spearman": None, "pearson": None, "rmse_pp": None,
     "note": "Pooled cross-validated r² (Fig. 5B); per-country 0.55–0.75, all within-country "
             "random k-fold. Restricted to households below twice the international poverty "
             "line, r² falls to about 0.12."},
    {"key": "jean_cons", "short": "Jean et al. 2016",
     "cite": "Jean et al. (2016), Science 353(6301):790–794",
     "doi": "10.1126/science.aaf7894",
     "target": "monetary", "target_label": "consumption expenditure",
     "unit": "survey clusters, 4 African countries", "n": None, "design": "randomcv",
     "r2": 0.45, "lo": 0.37, "hi": 0.55, "spearman": None, "pearson": None, "rmse_pp": None,
     "note": "Pooled cross-validated r² (Fig. 5A); per-country 0.37–0.55. Same imagery, same "
             "model, a monetary target instead of an asset one."},
    {"key": "yeh", "short": "Yeh et al. 2020",
     "cite": "Yeh, Perez, Driscoll, Azzari, Tang, Lobell, Ermon & Burke (2020), "
             "Nature Communications 11:2583",
     "doi": "10.1038/s41467-020-16185-w",
     "target": "asset", "target_label": "DHS asset wealth index",
     "unit": "19,669 villages, 23 African countries", "n": 19669, "design": "spatial",
     "r2": 0.67, "spearman": None, "pearson": None, "rmse_pp": None,
     "urban_r2": 0.40, "rural_r2": 0.32, "change_r2": 0.15, "consumption_r2": 0.50,
     "note": "Pooled r² at village level under held-out-country validation. Fitted separately "
             "within urban and rural strata it is 0.40 and 0.32; for change over time, 0.15. "
             "Their own asset index correlates with log consumption at only r² 0.50."},
    {"key": "chi", "short": "Chi et al. 2022",
     "cite": "Chi, Fang, Chatterjee & Blumenstock (2022), PNAS 119(3):e2113658119",
     "doi": "10.1073/pnas.2113658119",
     "target": "asset", "target_label": "relative wealth index",
     "unit": "2.4 km tiles, 135 countries", "n": None, "design": "spatial",
     "r2": 0.56, "lo": 0.56, "hi": 0.70, "spearman": None, "pearson": None, "rmse_pp": None,
     "note": "Explains 56–70% of household wealth variation depending on the evaluation; the "
             "index is explicitly relative to others in the same country."},
    {"key": "steele", "short": "Steele et al. 2017",
     "cite": "Steele, Sundsøy, Pezzulo, Alegana, Bird, Blumenstock, Bjelland, Engø-Monsen, "
             "de Montjoye, Iqbal et al. (2017), J. R. Soc. Interface 14(127):20160690",
     "doi": "10.1098/rsif.2016.0690",
     "target": "asset", "target_label": "DHS wealth index",
     "unit": "600 survey clusters, Bangladesh", "n": 600, "design": "randomcv",
     "r2": 0.76, "spearman": None, "pearson": None, "rmse_pp": None,
     "note": "Mobile-phone plus remote-sensing features, 80/20 hold-out."},
    {"key": "steele_ppi", "short": "Steele et al. 2017",
     "cite": "Steele et al. (2017), J. R. Soc. Interface 14(127):20160690",
     "doi": "10.1098/rsif.2016.0690",
     "target": "monetary", "target_label": "consumption poverty (PPI)",
     "unit": "600 survey clusters, Bangladesh", "n": 600, "design": "randomcv",
     "r2": 0.25, "spearman": None, "pearson": None, "rmse_pp": None,
     "urban_r2": 0.00, "rural_r2": 0.18,
     "note": "The identical features and units as the row above. In the urban subset the "
             "consumption-poverty r² is 0.00."},
    {"key": "engstrom", "short": "Engstrom et al. 2022",
     "cite": "Engstrom, Hersh & Newhouse (2022), World Bank Economic Review 36(2):382–412",
     "doi": "10.1093/wber/lhab015",
     "target": "monetary", "target_label": "poverty headcount rate",
     "unit": "1,291 GN divisions, Sri Lanka", "n": 1291, "design": "randomcv",
     "r2": 0.61, "spearman": None, "pearson": None, "rmse_pp": None,
     "asset_r2": 0.68, "survey_truth_r2": 0.217, "nightlights_only_r2": 0.20,
     "note": "Random tenfold CV against a census-imputed poverty rate averaged over 100 "
             "simulations. Against the raw survey estimate the same model scores 0.217."},
    {"key": "engstrom_sp", "short": "Engstrom et al. 2022",
     "cite": "Engstrom, Hersh & Newhouse (2022), World Bank Economic Review 36(2):382–412",
     "doi": "10.1093/wber/lhab015",
     "target": "monetary", "target_label": "poverty headcount, space held out",
     "unit": "1,291 GN divisions, Sri Lanka", "n": 1291, "design": "spatial",
     "r2": 0.45, "rf_r2": 0.5643, "spearman": 0.70, "pearson": None, "rmse_pp": None,
     "note": "Leave-one-Divisional-Secretariat-out. Linear 0.4498, random forest 0.5643 — the "
             "only published spatially held-out satellite model of a monetary poverty "
             "headcount, and the closest analogue to this case."},
]

# Paired studies that measured an asset target and a monetary target on the SAME data,
# same features, same units — the cleanest evidence on what target definition costs.
PAIRS = [
    {"study": "Steele et al. 2017", "where": "Bangladesh, 600 clusters",
     "asset": 0.76, "monetary": 0.25, "asset_label": "DHS wealth index",
     "monetary_label": "consumption poverty (PPI)"},
    {"study": "Jean et al. 2016", "where": "Africa, pooled cross-validated",
     "asset": 0.56, "monetary": 0.45, "asset_label": "DHS asset index",
     "monetary_label": "consumption expenditure"},
    {"study": "Engstrom et al. 2022", "where": "Sri Lanka, GN divisions",
     "asset": 0.68, "monetary": 0.61, "asset_label": "asset index",
     "monetary_label": "poverty headcount"},
]

# The measured cost of holding space out, where a study reports both designs on one dataset.
SPATIAL_PENALTY = [
    {"study": "Ploton et al. 2020", "field": "forest biomass, Central Africa",
     "random": 0.53, "spatial": 0.14,
     "doi": "10.1038/s41467-020-18321-y"},
    {"study": "Engstrom et al. 2022", "field": "poverty headcount, Sri Lanka",
     "random": 0.61, "spatial": 0.4498,
     "doi": "10.1093/wber/lhab015"},
]

# Indonesian small-area estimation with satellite auxiliaries — the official-method
# alternative at the level this case targets.
SAE = {
    "cite": "Feriyanto, Wijayanto, Wulansari & Parwanto (2024), Jurnal Aplikasi Statistika & "
            "Komputasi Statistik 16(2):205–221",
    "doi": "10.34123/jurnalasks.v16i2.799",
    "unit": "626 kecamatan, West Java", "n": 626,
    "mean_rse": 0.1039, "mean_rse_rs_only": 0.1048,
    "bps_good": 0.25, "bps_unusable": 0.50,
    "note": "EBLUP small-area estimation of per-capita expenditure on Susenas plus PODES with "
            "night lights and land-surface temperature as auxiliaries. BPS treats a relative "
            "standard error at or under 25% as good to use and over 50% as unreliable.",
}


def fam_of(col: str) -> str:
    if col.startswith("roof") or col.startswith("bld"):
        return "buildings"
    if col.startswith("lights"):
        return "lights"
    if col.startswith("lc_"):
        return "land cover"
    if col.startswith("pop") or col.startswith("log_pop"):
        return "population"
    return "geography"


def main() -> int:
    stats = json.loads((config.DATA_DIR / "stats.json").read_text())
    ms = json.loads((config.DATA_DIR / "model_stats.json").read_text())
    rv = json.loads((config.DATA_DIR / "review.json").read_text())

    gain = ms["gain_importance"]
    tot = sum(gain.values())
    fam: dict[str, float] = {}
    for k, v in gain.items():
        fam[fam_of(k)] = fam.get(fam_of(k), 0.0) + v
    families = sorted(({"k": k, "share": round(v / tot, 4)} for k, v in fam.items()),
                      key=lambda r: -r["share"])

    prov = rv["provinces"]
    ej = next((p for p in prov if p["name"] == "Jawa Timur"), None)

    # The poverty line's own geography, from the case's own BPS pull: BPS sets a line per
    # regency (and separately for urban and rural), so it varies INSIDE a province as well
    # as between them. A quantity that moves that much within a fold cannot be what makes
    # that fold's error a constant. §7 uses this.
    import pandas as pd

    f2 = pd.read_parquet(config.FEATURES_ADM2,
                         columns=["bps_code", "bps_name", "prov_code", "prov_name", "year",
                                  "poverty_line_idr"])
    lat = f2[(f2["year"] == stats["latest_year"]) & f2["poverty_line_idr"].notna()]
    g = lat.groupby("prov_code")["poverty_line_idr"]
    ratios = (g.max() / g.min()).dropna()
    worst = ratios.idxmax()
    w = lat[lat["prov_code"] == worst].sort_values("poverty_line_idr")
    line_geo = {
        "adm2_min": int(lat["poverty_line_idr"].min()),
        "adm2_max": int(lat["poverty_line_idr"].max()),
        "adm2_ratio": round(float(lat["poverty_line_idr"].max() / lat["poverty_line_idr"].min()), 3),
        "median_within_province_ratio": round(float(ratios.median()), 3),
        "max_within_province_ratio": round(float(ratios.max()), 3),
        "max_within_province": str(w["prov_name"].iloc[0]),
        "max_within_low_name": str(w["bps_name"].iloc[0]),
        "max_within_low": int(w["poverty_line_idr"].iloc[0]),
        "max_within_high_name": str(w["bps_name"].iloc[-1]),
        "max_within_high": int(w["poverty_line_idr"].iloc[-1]),
        "n_provinces_over_1_5x": int((ratios >= 1.5).sum()),
        "n_provinces": int(len(ratios)),
    }

    out = {
        "vintage": stats["vintage"],
        "generated": rv["generated"],
        "latest_year": stats["latest_year"],
        "ships": stats["ships"],
        "gates": {g["id"]: g for g in stats["gates"]},
        "thresholds": {"r2": config.GATE_R2_MIN, "spearman": config.GATE_SPEARMAN_MIN,
                       "rmse": config.GATE_RMSE_MAX_PP, "subgroup_r2": config.GATE_R2_OFFJAVA_MIN},
        "skill": stats["skill"],
        "panel": stats["skill_panel_lopo"],
        "folds": stats["folds"],
        "coverage": stats["coverage"],
        "decomposition": stats["decomposition"],
        "audit": rv["audit"],
        "recomputed": rv["recomputed"],
        "variance": rv["variance"],
        "withinProvince": rv["within_province"],
        "provinces": prov,
        "eastJava": ej,
        "offsetExplained": rv["offset_explained"],
        "lineGeography": line_geo,
        "calibration": rv["calibration"],
        "testA": rv["testA_ladder"],
        "testB": rv["testB_oracle"],
        "testC": rv["testC_line"],
        "testD": rv["testD_disaggregation"],
        "testE": rv["testE_persistence"],
        "surveyNoise": rv["survey_noise"],
        "coverageQuintiles": rv["coverage_quintiles"],
        "scatter": rv["scatter"],
        "families": families,
        "nFeatures": rv["n_features"],
        "featureFamilies": rv["families"],
        "literature": LITERATURE,
        "pairs": PAIRS,
        "spatialPenalty": SPATIAL_PENALTY,
        "sae": SAE,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1))
    a = out["testA"][-1]
    print(f"[article] LOPO R² {out['skill']['lopo']['r2']} · audit max Δ {out['audit']['max_abs_diff_r2']} · "
          f"ladder {len(out['testA'])} rungs (best {a['label']} {a['r2']}) · "
          f"{len(prov)} provinces · {len(out['scatter'])} dots")
    print(f"[article] -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
