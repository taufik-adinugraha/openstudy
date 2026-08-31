"""Build the review article's data layer from the case's own published outputs.

Every number the article prints comes from here — data/stats.json (what the dashboard
publishes), data/baserate.json (the three review tests) and data/linked.parquet (the
event table itself).  Nothing in the article is typed by hand, so the prose cannot
drift from the pipeline.

    uv run python pipeline/article.py            # -> web/src/data/article.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import config

OUT = config.CASE_DIR / "web" / "src" / "data" / "article.json"


def r(x, n=4):
    if x is None:
        return None
    v = float(x)
    return None if not np.isfinite(v) else round(v, n)


def main(argv: list[str]) -> int:
    stats = json.loads(config.STATS_JSON.read_text())
    br = json.loads((config.DATA_DIR / "baserate.json").read_text())
    lk = pd.read_parquet(config.LINKED)
    tot_ha = float(lk.ha.sum())

    cat, ls, ctl = br["catchment"], br["loss_split"], br["controls"]
    ef, pe, idn = ctl["event_floor"], ctl["palm_extent"], ctl["identifiability"]

    out: dict = {
        "generated": br.get("generated"),
        "stats_generated": stats.get("generated"),
        "vintages": stats["vintages"],
        "licences": stats["licences"],
        "citations": stats["citations"],
        "record": {
            "events": int(len(lk)),
            "ha": tot_ha,
            "first_date": str(lk.first_date.min())[:10],
            "last_date": str(lk.last_date.max())[:10],
            "min_cluster_ha": config.MIN_CLUSTER_HA,
            "mill_radius_km": config.MILL_RADIUS_KM,
            "n_mills": cat["n_mills"],
            "dropped_outside_events": stats["clusters"].get("dropped_outside_indonesia_events"),
            "dropped_outside_ha": stats["clusters"].get("dropped_outside_indonesia_ha"),
            "hi_conf_ha_share": r(lk.loc[lk.hi_share >= 0.5].ha.sum() / tot_ha),
            "peat_share": r(stats["linkage"]["peat_share"]),
            "peat_in_catchment": r(lk.loc[lk.on_peat & (lk.mill_dist_km <= 50)].ha.sum()
                                   / lk.loc[lk.on_peat].ha.sum()),
            "by_class_share": {k: r(v) for k, v in stats["linkage"]["by_class_share"].items()},
            "by_class_ha": stats["linkage"]["by_class_ha"],
            "linked_share_ha": r(stats["linkage"]["linked_share"]),
            "linked_share_events": r(float((lk.mill_dist_km <= 50).mean())),
        },
        "gates": stats["gates"],
        "divergence": stats["divergence"],
    }

    # ── 1 · tree-cover loss decomposed by what the land already was ────────────────────
    years = ls["years"]
    cls = ls["by_plantation_class_ha"]
    out["loss"] = {
        "years": years,
        "total_ha": [r(ls["total_ha"][str(y)], 0) for y in years],
        "oil_palm_ha": [r(cls.get("oil_palm", {}).get(str(y), 0.0), 0) for y in years],
        "wood_fibre_ha": [r(cls.get("wood_fibre", {}).get(str(y), 0.0), 0) for y in years],
        "rubber_ha": [r(cls.get("rubber", {}).get(str(y), 0.0), 0) for y in years],
        "outside_plantation_ha": [r(cls.get("none", {}).get(str(y), 0.0), 0) for y in years],
        "primary_2001_ha": [r(ls["primary_2001_ha"][str(y)], 0) for y in years],
        "primary_outside_plantation_ha": [r(ls["primary_outside_plantation_ha"][str(y)], 0)
                                          for y in years],
        "in_plantation_share": [r(ls["in_plantation_share"][str(y)]) for y in years],
        "grand_total_ha": r(sum(ls["total_ha"].values()), 0),
        "grand_plantation_ha": r(sum(ls["in_plantation_ha"].values()), 0),
        "grand_plantation_share": r(sum(ls["in_plantation_ha"].values())
                                    / sum(ls["total_ha"].values())),
        "grand_by_class_mha": {k: r(sum(v.values()) / 1e6, 3) for k, v in cls.items()},
        "share_first": r(ls["in_plantation_share"][str(years[0])]),
        "share_peak": r(max(ls["in_plantation_share"].values())),
        "share_peak_year": int(max(ls["in_plantation_share"],
                                   key=lambda k: ls["in_plantation_share"][k])),
        "share_last": r(ls["in_plantation_share"][str(years[-1])]),
        "gfw_published_primary_loss": ls["gfw_published_primary_loss_ha"],
        "our_primary_loss": {y: r(ls["primary_2001_ha"][y], 0)
                             for y in ls["gfw_published_primary_loss_ha"]},
        "primary_reconciliation_pct": {
            y: r(100 * (ls["primary_2001_ha"][y] / v - 1), 2)
            for y, v in ls["gfw_published_primary_loss_ha"].items()},
        "method": ls["method"],
    }

    # ── 2 · the catchment base rate ────────────────────────────────────────────────────
    bins = cat["bins_km"]
    h = cat["hist"]
    unf = ef["unfiltered_hist_ha"]

    def cum(a):
        s = float(sum(a))
        c, run = [], 0.0
        for v in a:
            run += v
            c.append(r(run / s) if s else None)
        return c

    out["catchment"] = {
        "bins_km": bins,
        "cum": {"land": cum(h["land_ha"]), "domain": cum(h["domain_ha"]),
                "alert": cum(h["alert_ha"]), "unfiltered": cum(unf)},
        "land_ha": r(cat["land_ha"], 0),
        "domain_ha": r(cat["domain_ha"], 0),
        "domain_share_of_land": r(cat["domain_share_of_land"]),
        "national": {k: {kk: r(vv) for kk, vv in v.items()}
                     for k, v in cat["national"].items()},
        "radii": sorted(int(k) for k in cat["national"]),
        "method": cat["method"],
        "lattice_m": cat["lattice_m"],
    }
    n50 = cat["national"]["50"]
    out["catchment"]["headline"] = {
        "domain_base": r(n50["domain_base"]), "land_base": r(n50["land_base"]),
        "alert": r(n50["alert_ha_share"]), "alert_events": r(n50["alert_event_share"]),
        "lift": r(n50["lift_vs_domain"], 2),
        "unfiltered": r(ef["unfiltered_within_50km"]),
        "unfiltered_lift": r(ef["unfiltered_within_50km"] / n50["domain_base"], 2),
    }
    best = max(cat["national"].items(), key=lambda kv: kv[1]["lift_vs_domain"] or 0)
    out["catchment"]["best_radius"] = {"km": int(best[0]),
                                       "lift": r(best[1]["lift_vs_domain"], 2),
                                       "base": r(best[1]["domain_base"]),
                                       "alert": r(best[1]["alert_ha_share"])}
    prov = [{"province": k, **{kk: r(vv) for kk, vv in v.items()}}
            for k, v in cat["by_province"].items()]
    prov = [p for p in prov if (p["alert_ha"] or 0) >= 5000]
    prov.sort(key=lambda p: -(p["alert_ha"] or 0))
    out["catchment"]["provinces"] = prov

    # ── 3 · the event floor ────────────────────────────────────────────────────────────
    out["floor"] = {
        "grid_deg": ef["grid_deg"],
        "unfiltered_ha": r(ef["unfiltered_ha"], 0),
        "event_table_ha": r(ef["event_table_ha"], 0),
        "keeps": r(ef["event_floor_keeps"]),
        "filtered_within_50": r(ef["events_within_50km"]),
        "unfiltered_within_50": r(ef["unfiltered_within_50km"]),
        "gap_pp": r(100 * (ef["events_within_50km"] - ef["unfiltered_within_50km"]), 1),
        "by_size": [{**b, "within_50km_ha_share": r(b["within_50km_ha_share"]),
                     "median_mill_km": r(b["median_mill_km"], 1),
                     "ha": r(b["ha"], 0)} for b in ef["by_size"]],
        "gate_keeps_focus_provinces": r(
            stats["gates"]["G-H2"].get("event_floor_keeps_share_of_ha")),
        "note": ef["note"],
    }

    # ── 4 · identifiability ────────────────────────────────────────────────────────────
    mc = idn["mills_claiming_a_hectare"]
    rec = lk.loc[lk.first_date >= lk.last_date.max() - pd.Timedelta(days=365)]
    scored = (pd.read_parquet(config.MILLS_SCORED) if config.MILLS_SCORED.exists()
              else pd.DataFrame(columns=["alert_ha_12m"]))
    in_catch_12m = float(rec.loc[rec.mill_dist_km <= 50].ha.sum())
    out["identifiability"] = {
        "mills": {k: r(v, 3) for k, v in mc.items()},
        "pressure_sum_ha": r(float(scored.alert_ha_12m.sum()) if len(scored) else 0.0, 0),
        "in_catchment_12m_ha": r(in_catch_12m, 0),
        "alert_12m_ha": r(float(rec.ha.sum()), 0),
        "double_count": r(float(scored.alert_ha_12m.sum()) / in_catch_12m, 2)
        if len(scored) and in_catch_12m else None,
        "n_mills_with_pressure": int((scored.alert_ha_12m > 0).sum()) if len(scored) else 0,
        "n_mills": int(len(scored)),
    }

    # ── 5 · what the sensor can see ────────────────────────────────────────────────────
    JAVA = ("Banten", "West Java", "Central Java", "East Java", "Yogyakarta",
            "Jakarta Special Capital Region")
    NUSA = ("West Nusa Tenggara", "East Nusa Tenggara", "Bali")
    byp = lk.groupby("province").ha.sum()
    out["deforested_islands"] = {
        "java_ha": r(float(byp.reindex(JAVA).fillna(0).sum()), 1),
        "java_share": r(float(byp.reindex(JAVA).fillna(0).sum()) / tot_ha, 5),
        "nusa_ha": r(float(byp.reindex(NUSA).fillna(0).sum()), 1),
        "nusa_share": r(float(byp.reindex(NUSA).fillna(0).sum()) / tot_ha, 5),
        "provinces_with_alerts": int((byp > 0).sum()),
        "provinces_total": int(cat.get("n_provinces", 34)),
        "smallest": [{"province": p, "ha": r(v, 1)} for p, v in byp.nsmallest(8).items()],
    }
    out["domain"] = {
        "primary_share_of_alert_ha": r(stats["radd_domain"]["primary_share_of_alert_ha"]),
        "domain_share_of_land": r(cat["domain_share_of_land"]),
        "sdpt_palm_ha": pe["sdpt_ha"]["oil_palm"],
        "sdpt_wood_fibre_ha": pe["sdpt_ha"]["wood_fibre"],
        "sdpt_rubber_ha": pe["sdpt_ha"]["rubber"],
        "palm_in_domain_share": r(pe["sdpt_palm_in_radd_domain_share"]),
        "palm_in_domain_ha": pe["sdpt_palm_in_radd_domain_ha"],
        "descals_mapped_mha": pe["descals_2021_idn_mapped_mha"],
        "descals_estimate_mha": pe["descals_2021_idn_estimate_mha"],
        "gaveau_mapped_mha": pe["gaveau_2022_idn_mapped_mha"],
        "gaveau_adjusted_mha": pe["gaveau_2022_idn_adjusted_mha"],
        "sdpt_vs_descals": r(pe["sdpt_ha"]["oil_palm"] / 1e6 / pe["descals_2021_idn_mapped_mha"],
                             3),
        "sdpt_vs_gaveau": r(pe["sdpt_ha"]["oil_palm"] / 1e6 / pe["gaveau_2022_idn_mapped_mha"],
                            3),
    }

    # ── 6 · the two sensors ────────────────────────────────────────────────────────────
    rl = idn["radar_lead_days"]
    out["sensors"] = {
        "agree_all_events": r(float(lk.glad_agree.mean())),
        "agree_all_ha": r(float(lk.loc[lk.glad_agree].ha.sum() / tot_ha)),
        "gate_share": r(stats["gates"]["G-H3"]["agreement_share"]),
        "gate_n": stats["gates"]["G-H3"]["n_clusters"],
        "gate_event_share": r(stats["gates"]["G-H3"]["n_clusters"] / len(lk)),
        "gate_ha_share": r(float(lk.loc[(lk.ha >= 5) & (lk.hi_share >= 0.5)].ha.sum() / tot_ha)),
        "by_size": [{**b, "agree": r(b["agree"])} for b in idn["glad_agreement_by_size"]],
        "lead": {k: (r(v, 2) if isinstance(v, (int, float)) and not isinstance(v, bool) else v)
                 for k, v in rl.items() if k not in ("hist", "hist_edges", "note")},
        "lead_hist": rl["hist"],
        "lead_edges": rl["hist_edges"],
        "window_days": config.GLAD_AGREEMENT_DAYS,
    }

    # ── 7 · the weekly record, for the archive-start artefact ─────────────────────────
    wk = (lk.assign(w=lk.first_date.dt.to_period("W-SUN").dt.start_time)
          .groupby("w").ha.sum().sort_index())
    out["weekly"] = {
        "weeks": [str(x)[:10] for x in wk.index],
        "ha": [r(v, 1) for v in wk.to_numpy()],
        "median": r(float(wk.median()), 1),
        "first_two_ha": r(float(wk.iloc[:2].sum()), 0),
        "first_two_vs_median": r(float(wk.iloc[:2].sum() / 2 / wk.median()), 2),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1))
    hh = out["catchment"]["headline"]
    print(f"[article] base {hh['domain_base']:.3f} · observed {hh['alert']:.3f} · "
          f"lift {hh['lift']:.2f} · unfiltered {hh['unfiltered']:.3f} "
          f"(lift {hh['unfiltered_lift']:.2f})")
    print(f"[article] loss 2001-{years[-1]}: {out['loss']['grand_plantation_share']:.1%} "
          f"inside mapped plantation")
    print(f"[article] -> {OUT} ({OUT.stat().st_size / 1024:.0f} kB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
