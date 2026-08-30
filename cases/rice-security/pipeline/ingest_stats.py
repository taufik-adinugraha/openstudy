"""Stage 1 · stats — BPS official rice statistics, the thing this case is benchmarked against.

THIS STAGE RUNS FIRST ON PURPOSE.  It is cheap, and if the official series cannot be retrieved
there is nothing to validate against and the case does not exist.  Failing here costs minutes;
failing here after the SAR ingest costs days.

BPS WebAPI, the parts that are not in the documentation
-------------------------------------------------------
  * ``th`` is REQUIRED on ``model=data`` and the error says so only after the request:
    "'th' parameter is required and must be an integer, separated by colon (:) for range or
    semicolon (;) for multiple values."  It takes a range (``th/118:119``) capped at TWO years
    per call, which halves the crawl.
  * ``keyword`` works on the ``var`` model — ``list/model/var/domain/3200/keyword/padi`` — which
    is how the variable ids below are resolved rather than remembered.
  * A BROWSER User-Agent is required; the WAF rejects curl-style agents with something that
    looks like a server error rather than a block.  The "App ID" IS the API key.
  * ``datacontent`` keys are POSITIONAL CONCATENATION, not a delimited encoding:
    ``str(vervar) + str(var) + str(turvar) + str(th) + str(turtahun)``.  ``3200935011810`` is
    vervar 3200, var 935, turvar 0, th 118 (=2018), turtahun 10 (=Oktober).  There is no
    separator and the fields are variable width, so the only safe parse is to BUILD every
    candidate key from the lookup lists the same response returns and index into the dict.
    Splitting on length or on a fixed offset works on one table and silently mis-assigns on the
    next.  ``th`` id = calendar year - 1900.

** RICE IS NOT LIKE THE POVERTY VARIABLES. **  The house gotcha "national domain 0000 serves all
514 regencies" is true for poverty and FALSE here: on domain 0000 every rice table's vervar group
is "38 Provinsi".  Kabupaten rice lives only on the PROVINCIAL domains, with a different var id
and a different table shape per province — Jawa Barat 3200 var 935 and Jawa Timur 3500 var 578
publish MONTHLY harvested area per regency 2018-2025, which is the 65-regency x 96-month,
6,240-observation panel that makes gate G-I2 testable at kabupaten level.  Jawa Tengah (3300 var
463) is annual only, so Grobogan is validated annually and that is stated wherever it appears.

THE METHODOLOGY BREAK THAT MUST NOT BE SMOOTHED OVER
----------------------------------------------------
BPS replaced eye-estimate harvested area with the KSA area-frame sample from the 2018 reference
year.  2016 and 2017 are a moratorium with no data at all, so the break is a two-year HOLE with a
2.74 Mha (-19.4 %) step-down across it: 14,116,638 ha in 2015, nothing, 11,377,934 ha in 2018.
Any line drawn through that hole is a lie about a trend that is a definitional change.

OUTPUT: data/bps_kab_month.parquet, data/bps_kab_year.parquet, data/bps_prov.parquet,
data/bps_national_month.parquet, data/bps_prices.parquet, data/bps_vars.json,
data/bps_checks.json (the three known defects, asserted rather than inherited).
"""

from __future__ import annotations

import json
import time

import config
import util
from util import log

OUT = config.DATA_DIR
MONTHS = {1: "Januari", 2: "Februari", 3: "Maret", 4: "April", 5: "Mei", 6: "Juni",
          7: "Juli", 8: "Agustus", 9: "September", 10: "Oktober", 11: "November",
          12: "Desember", 13: "Tahunan"}


def _get(url: str, tries: int = 4):
    import requests

    for attempt in range(tries):
        try:
            r = requests.get(url, headers=util.browser_ua(), timeout=120)
            r.raise_for_status()
            j = r.json()
            if j.get("status") == "OK" or j.get("data-availability") == "available":
                return j
            if "not available" in str(j.get("message", "")).lower():
                return None
            raise RuntimeError(j.get("message", j.get("status")))
        except Exception as exc:                              # noqa: BLE001
            log(f"bps: {type(exc).__name__} {exc} (attempt {attempt + 1}/{tries})")
            time.sleep(2 + 3 * attempt)
    return None


def list_variables(domain: str) -> list[dict]:
    """Rice-related variables on a domain, found by keyword rather than remembered."""
    found: dict[int, dict] = {}
    for term in config.BPS_SEARCH_TERMS:
        url = (f"{config.BPS_BASE}/list/model/var/lang/ind/domain/{domain}/"
               f"keyword/{term}/key/{config.BPS_API_KEY}/")
        j = _get(url)
        time.sleep(config.BPS_SLEEP_S)
        if not j:
            continue
        data = j.get("data") or []
        rows = data[1] if len(data) > 1 and isinstance(data[1], list) else []
        for v in rows:
            found.setdefault(int(v["var_id"]), {"var_id": int(v["var_id"]),
                                                "title": v.get("title"),
                                                "unit": v.get("unit"),
                                                "subject": v.get("sub_name"),
                                                "found_by": term})
    return sorted(found.values(), key=lambda d: d["var_id"])


def fetch_series(domain: str, var_id: int, years: tuple[int, int]):
    """One variable, all regions, a year range — decoded into tidy rows.

    Every candidate key is BUILT from the lookup lists in the same payload (see the module
    docstring); anything not present in ``datacontent`` is a genuine hole and is skipped rather
    than filled.
    """
    import pandas as pd

    rows: list[dict] = []
    meta = {}
    y0, y1 = years
    for a in range(y0, y1 + 1, config.BPS_MAX_YEARS_PER_CALL):
        b = min(a + config.BPS_MAX_YEARS_PER_CALL - 1, y1)
        th = f"{a - 1900}:{b - 1900}" if b > a else f"{a - 1900}"
        url = (f"{config.BPS_BASE}/list/model/data/lang/ind/domain/{domain}/var/{var_id}/"
               f"th/{th}/key/{config.BPS_API_KEY}/")
        j = _get(url)
        time.sleep(config.BPS_SLEEP_S)
        if not j:
            log(f"bps: domain {domain} var {var_id} th {th} unavailable")
            continue
        dc = j.get("datacontent") or {}
        vervar = j.get("vervar") or []
        turvar = j.get("turvar") or [{"val": "0", "label": "Tidak ada"}]
        tahun = j.get("tahun") or []
        turtahun = j.get("turtahun") or [{"val": 0, "label": "Tahun"}]
        meta = {"title": (j.get("var") or [{}])[0].get("label"),
                "unit": (j.get("var") or [{}])[0].get("unit"),
                "note": (j.get("var") or [{}])[0].get("note"),
                "turvar": {str(t["val"]): t["label"] for t in turvar},
                "n_vervar": len(vervar)}
        for vv in vervar:
            for tv in turvar:
                for th_ in tahun:
                    for tt in turtahun:
                        key = f"{vv['val']}{var_id}{tv['val']}{th_['val']}{tt['val']}"
                        if key not in dc:
                            continue
                        rows.append(dict(domain=domain, var=var_id,
                                         region=int(vv["val"]), region_name=vv["label"],
                                         turvar=str(tv["val"]), turvar_label=tv["label"],
                                         year=int(th_["label"]),
                                         turtahun=int(tt["val"]),
                                         period=tt["label"], value=dc[key]))
    return pd.DataFrame(rows), meta


def _clean_name(s: str) -> str:
    """Strip BPS's inconsistent label decoration.

    Jawa Barat labels a regency ``Bogor``, Jawa Timur ``Kabupaten Pacitan`` and Jawa Tengah
    ``3315 Kabupaten Grobogan`` — the code is *inside the label* on some provincial tables and
    not on others.  Leaving it in means ``"Grobogan" in SCOPE_DEEP`` is False and the rainfed
    contrast case silently drops out of every join.
    """
    import re

    s = re.sub(r"^\s*\d{4,5}\s+", "", str(s))
    for junk in ("Kabupaten ", "Kota ", "KABUPATEN ", "KOTA ", "Provinsi ", "PROVINSI "):
        s = s.replace(junk, "")
    return " ".join(s.split()).strip().title()


def main() -> None:
    import numpy as np
    import pandas as pd

    util.require(bool(config.BPS_API_KEY), "BPS_API_KEY missing from repo-root .env")
    OUT.mkdir(parents=True, exist_ok=True)
    resolved = {"resolved_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "domains": {}, "series": {}}

    # ── 0. the INDEPENDENT provincial totals, pulled first because they are the referee ──
    # Domain 0000 var 1498 (annual by province, 2018-2024) and var 2504 (monthly by province,
    # 2025-2026) come from a different table on a different domain from the provincial
    # kabupaten tables.  Every provincial kabupaten table is checked against them below, which
    # is how the Jawa Tengah 2024/2025 corruption was caught.
    prov_frames = []
    d, meta = fetch_series(config.BPS_DOMAIN_NATIONAL, 1498, (2018, 2024))
    resolved["series"]["national:1498"] = {**meta, "rows": int(len(d))}
    if len(d):
        d["quantity"] = d["turvar"].map(dict(meta["turvar"]))
        d["month"] = 0
        prov_frames.append(d[["region", "region_name", "year", "month", "quantity", "value"]])
    d2, meta2 = fetch_series(config.BPS_DOMAIN_NATIONAL, 2504, (2025, 2026))
    resolved["series"]["national:2504"] = {**meta2, "rows": int(len(d2))}
    if len(d2):
        d2["quantity"] = "Luas Panen (ha)"
        d2["month"] = d2["turtahun"].where(d2["turtahun"] <= 12, 0)
        prov_frames.append(d2[["region", "region_name", "year", "month", "quantity", "value"]])
    prov = pd.concat(prov_frames, ignore_index=True)
    prov["province"] = prov["region_name"].map(_clean_name)
    prov = prov[prov["region"] != 9999]                 # 9999 INDONESIA is a total, not a unit
    prov.to_parquet(OUT / "bps_prov.parquet", index=False)
    log(f"stats: provincial referee table {len(prov):,} rows "
        f"({prov['year'].min()}-{prov['year'].max()}, {prov['province'].nunique()} provinces)")
    # annual provincial harvested area, recomputed from monthly cells where they exist
    pa = prov[prov["quantity"].str.contains("Luas Panen", na=False)]
    ref = {}
    for (p_, y), g in pa.groupby(["province", "year"]):
        m = g[g["month"] > 0]["value"].sum()
        ref[(p_, int(y))] = float(m if m > 0 else g[g["month"] == 0]["value"].sum())

    # ── 1. kabupaten monthly — the panel gate G-I2 is evaluated on ────────────────────
    kab_rows = []
    for pname, spec in config.BPS_PROVINCE_TABLES.items():
        if pname not in config.SCOPE_PROVINCES:
            continue
        dom = spec["domain"]
        resolved["domains"][pname] = {"domain": dom,
                                      "vars_matching_rice": list_variables(dom)[:40]}
        df, meta = fetch_series(dom, spec["area_var"], spec["years"])
        util.require(len(df) > 0,
                     f"{pname}: no rows for var {spec['area_var']} on domain {dom}")
        resolved["series"][f"{pname}:area"] = {"domain": dom, "var": spec["area_var"],
                                               "period": spec["period"], **meta,
                                               "rows": int(len(df))}
        df["province"] = pname
        # Jawa Tengah is annual and puts the quantity in turvar; the monthly provinces put the
        # month in turtahun.  Normalise both onto (year, month) with month 0 = annual.
        if spec["period"] == "annual":
            area_tv = next((k for k, v in meta["turvar"].items()
                            if "luas panen" in str(v).lower()), None)
            util.require(area_tv is not None,
                         f"{pname}: no 'Luas Panen' turvar in {meta['turvar']}")
            keep = df[df["turvar"] == area_tv].copy()
            keep["month"] = 0
        else:
            keep = df.copy()
            keep["month"] = keep["turtahun"].where(keep["turtahun"] <= 12, 0)
        keep["kab"] = keep["region_name"].map(_clean_name)
        keep["is_province_row"] = keep["region"] % 100 == 0
        kab_rows.append(keep[["province", "domain", "var", "region", "kab", "year", "month",
                              "value", "is_province_row"]])
        log(f"stats: {pname} var {spec['area_var']} -> {len(keep):,} rows "
            f"({keep['region'].nunique()} units, {keep['year'].min()}-{keep['year'].max()})")

    kab = pd.concat(kab_rows, ignore_index=True)
    kab = kab.rename(columns={"value": "ha"})

    # ── 2. the three published defects, ASSERTED rather than inherited ────────────────
    checks = {}
    jt = kab[(kab["province"] == "Jawa Timur") & (kab["year"] == 2025)]
    batu = jt[jt["kab"].str.upper().str.contains("BATU")]
    ann = batu[batu["month"] == 0]["ha"].sum()
    mon = batu[batu["month"] > 0]["ha"].sum()
    checks["kota_batu_2025"] = {
        "annual_cell": float(ann), "sum_of_months": float(mon),
        "ratio": round(float(ann / mon), 2) if mon else None,
        "verdict": ("100x decimal slip confirmed — annual cells are DISCARDED and every annual "
                    "total is recomputed from the monthly cells"
                    if mon and 50 < ann / mon < 200 else
                    "not reproduced in this pull; annual is recomputed from monthly regardless")}
    log(f"stats: Kota Batu 2025 annual={ann:,.1f} vs monthly sum={mon:,.1f} "
        f"-> {checks['kota_batu_2025']['verdict'][:60]}")

    # Annual is ALWAYS recomputed from monthly where monthly exists (defect "stale_tahunan").
    monthly = kab[kab["month"] > 0]
    annual_from_month = (monthly.groupby(["province", "region", "kab", "year"], as_index=False)
                         ["ha"].sum().assign(source="recomputed from monthly cells"))
    annual_native = (kab[(kab["month"] == 0) &
                         ~kab.set_index(["province", "region", "year"]).index.isin(
                             annual_from_month.set_index(["province", "region", "year"]).index)]
                     [["province", "region", "kab", "year", "ha"]]
                     .assign(source="published annual cell (province has no monthly table)"))
    kab_year = pd.concat([annual_from_month, annual_native], ignore_index=True)

    # kabupaten rows must sum to the provincial row — three-way consistency, verified live
    cons = []
    for (prov, year), g in kab_year.groupby(["province", "year"]):
        prow = g[g["region"] % 100 == 0]["ha"].sum()
        krow = g[g["region"] % 100 != 0]["ha"].sum()
        if prow > 0:
            cons.append(dict(province=prov, year=int(year), province_row=float(prow),
                             kabupaten_sum=float(krow),
                             diff_pct=round(100 * (krow - prow) / prow, 3)))
    checks["kabupaten_sum_vs_province_row"] = cons
    worst = max((abs(c["diff_pct"]) for c in cons), default=0.0)
    log(f"stats: kabupaten rows vs provincial row — worst |diff| {worst:.3f} %")

    blank = (monthly.assign(k=1).groupby(["province", "region", "year"])["month"].nunique()
             .reset_index())
    checks["blank_regency_months"] = {
        "expected_months_per_regency_year": 12,
        "regency_years_with_gaps": int((blank["month"] < 12).sum()),
        "note": config.BPS_KNOWN_DEFECTS["blank_not_missing"],
        "treatment": "left as missing, never imputed as zero; the gate excludes them and says so",
    }

    # ── 2b. A FOURTH DEFECT, found here and not in the reconnaissance ─────────────────
    # Jawa Tengah's var 463 is sound for 2018-2023 — the regency rows sum EXACTLY to its own
    # provincial row, which matches the independent national table to the cent.  For 2024 and
    # 2025 both halves break at once: the provincial row collapses to 106,347 / 125,882 ha
    # (about one regency's worth against a true ~1.55 / ~1.67 Mha) while the regency rows sum
    # to 3.00 / 3.22 Mha, roughly 1.93x the truth.  Productivity in the same cells is fine, so
    # it is not a unit change.  Grobogan's own cells move with it: 129,631 ha in 2023 to
    # 84,846 in 2024, a 35 % drop that no agronomy supports.
    # Treatment: every (province, year) is refereed against the national provincial table and
    # a failure marks the year UNUSABLE as a benchmark rather than being silently ingested.
    ref_rows = []
    for (p_, y), g in kab_year.groupby(["province", "year"]):
        got = float(g[g["region"] % 100 != 0]["ha"].sum())
        want = ref.get((p_, int(y)))
        ok = want is not None and want > 0 and abs(got - want) / want <= 0.02
        ref_rows.append(dict(province=p_, year=int(y), kabupaten_sum=got,
                             national_table=want,
                             diff_pct=None if not want else round(100 * (got - want) / want, 2),
                             usable=bool(ok)))
    checks["refereed_against_national_table"] = ref_rows
    bad = [(r["province"], r["year"]) for r in ref_rows if not r["usable"]]
    checks["jawa_tengah_2024_25_defect"] = {
        "found": [f"{p} {y}" for p, y in bad],
        "description": ("domain 3300 var 463: for 2024 and 2025 the provincial row is ~1/15th "
                        "of the provincial total while the regency rows sum to ~1.93x it. "
                        "2018-2023 reconcile exactly. Not previously catalogued."),
        "treatment": "those (province, year) cells are excluded from every gate, and said so",
    }
    kab_year["benchmark_usable"] = [
        (r.province, int(r.year)) not in bad for r in kab_year.itertuples()]
    kab["benchmark_usable"] = [
        (r.province, int(r.year)) not in bad for r in kab.itertuples()]
    log(f"stats: referee vs the national table — unusable (province, year): {bad or 'none'}")

    kab.to_parquet(OUT / "bps_kab_month.parquet", index=False)
    kab_year.to_parquet(OUT / "bps_kab_year.parquet", index=False)

    # ── 3. national context ──────────────────────────────────────────────────────────
    nat, metan = fetch_series(config.BPS_DOMAIN_NATIONAL, 2345, (2018, 2026))
    nat = nat[nat["region"] == 1]                      # vervar 1 = Padi
    nat["month"] = nat["turtahun"]
    nat.to_parquet(OUT / "bps_national_month.parquet", index=False)
    resolved["series"]["national:2345"] = {**metan, "rows": int(len(nat))}
    stale = nat[(nat["year"] == 2026) & (nat["month"] == 13)]["value"].sum()
    jan_apr = nat[(nat["year"] == 2026) & (nat["month"].between(1, 4))]["value"].sum()
    checks["stale_tahunan_2345"] = {"annual_cell_2026": float(stale),
                                    "jan_apr_sum_2026": float(jan_apr),
                                    "equal": bool(abs(stale - jan_apr) < 1),
                                    "treatment": "annual always recomputed from monthly cells"}

    price, metap = fetch_series(config.BPS_DOMAIN_NATIONAL, 295, (2017, 2026))
    if len(price):
        price.to_parquet(OUT / "bps_prices.parquet", index=False)
        resolved["series"]["national:295"] = {**metap, "rows": int(len(price))}
        log(f"stats: wholesale rice price series {len(price):,} rows")

    # ── 4. the break, from BPS's own numbers, stored as two regimes ───────────────────
    checks["ksa_break"] = {**{k: v for k, v in config.BPS_BREAK.items()
                              if k != "ksa_series_mha"},
                           "step_ha": config.BPS_BREAK["first_ksa_ha"] -
                                      config.BPS_BREAK["last_sp_ha"],
                           "step_pct": round(100 * (config.BPS_BREAK["first_ksa_ha"] /
                                                    config.BPS_BREAK["last_sp_ha"] - 1), 1),
                           "rule": "no line may cross 2015-2018; the moratorium is drawn as a hole"}
    nat_year: dict[int, float] = {}
    for (p_, y), v in ref.items():
        nat_year[y] = nat_year.get(y, 0.0) + v
    checks["national_annual_ha_summed_from_provinces"] = {
        int(k): round(v, 1) for k, v in sorted(nat_year.items())}
    checks["bps_2025_jump"] = {
        "national_2024_ha": round(nat_year.get(2024, 0), 1),
        "national_2025_ha": round(nat_year.get(2025, 0), 1),
        "delta_ha": round(nat_year.get(2025, 0) - nat_year.get(2024, 0), 1),
        "delta_pct": (round(100 * (nat_year.get(2025, 0) / nat_year.get(2024, 1) - 1), 1)
                      if nat_year.get(2024) else None),
        "spec_expected_delta_ha": config.BPS_2025_JUMP_HA,
        "note": "the single most stress-testable official claim in the series — chapter 03",
    }

    (OUT / "bps_checks.json").write_text(json.dumps(checks, indent=1, default=str))
    (OUT / "bps_vars.json").write_text(json.dumps(resolved, indent=1, default=str))
    log(f"stats: wrote {OUT/'bps_kab_month.parquet'} ({len(kab):,} rows), "
        f"kab_year ({len(kab_year):,}), prov ({len(prov):,}), national ({len(nat):,})")

    deep = kab_year[kab_year["kab"].isin(config.SCOPE_DEEP)]
    for kabname, g in deep.groupby("kab"):
        yr = g[g["year"] == 2025]["ha"].sum()
        want = config.SCOPE_DEEP[kabname].get("ha_2025")
        log(f"stats: {kabname:11s} 2025 harvested {yr:,.0f} ha"
            + (f" (spec said {want:,})" if want else " (annual-only province)"))


if __name__ == "__main__":
    main()
