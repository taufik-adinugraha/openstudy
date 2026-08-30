"""Stage 9 · validate — gates G-J1..G-J5, written before any result was seen.

The thresholds live in ``config`` and were fixed at spec time.  A gate that fails is published
red with a diagnosis (the house precedent: Case C's G-C5 and Case H's G-H1 both ship failing and
explained).  Thresholds are never moved to fit an outcome; if one turns out to have been the
wrong question, the spec is amended in a dated entry and both the old and new results stand.

G-J1  Hotspot hygiene (HARD).  Zero retained detections inside the static exclusion mask, on
      EVERY product including NRT — which is the point, because ``type`` does not exist in NRT
      and a field-based filter would pass this gate while doing nothing to the live tail.  The
      removed share is published with its composition.  Measured expectation at the 2019 peak:
      ~0.9 % by type and ~5.7 % by low confidence; away from the peak the constant volcano and
      flare floor is a much larger fraction.  A removal share below
      ``config.GATE_REMOVED_MIN_SHARE`` FAILS the gate: it is evidence the filter is broken, not
      evidence that Indonesia has no volcanoes.

G-J2  Ignition-risk skill (HARD).  On the held-out seasons: AUC >= ``config.GATE_AUC`` and a
      Brier skill score > ``config.GATE_BSS`` against BOTH the per-cell day-of-year climatology
      AND the CEMS Canadian Fire Weather Index, at every lead in ``config.LEAD_DAYS``.  Beating
      climatology is table stakes; the FWI is an external, operational index we did not design
      and cannot tune, so it is the comparison that means something.  Reported per lead and
      separately for the reanalysis path and the CHIRPS-GEFS forecast path — the gap between
      them is the real cost of forecasting rather than hindcasting, and it is published.

G-J3  Transport direction.  On observed episode days at each receptor, the bearing from receptor
      to the back-trajectory's fire-weighted centroid must agree with the bearing implied by the
      forward run within ``config.GATE_BEARING_DEG`` on at least ``config.GATE_BEARING_SHARE`` of
      those days.  This is an internal-consistency gate: it proves the integrator, not the
      physics, and the spec says so in those words.  The independent check on the physics is the
      CAMS comparison, which is reported as a divergence chart rather than as a score.

G-J4  Receptor correlation (HARD AT TIER 1 ONLY).  Daily modelled exposure vs observed PM2.5
      over the fire seasons: Spearman rho >= ``config.GATE_RHO`` at Singapore, which is the only
      receptor with a long, clean, commercially-licensed instrument record.  Tier-2 Indonesian
      stations are reported with their short coverage stated; tier-3 receptors are compared
      against the CAMS EAC4 reanalysis and labelled model-vs-model, never observation.  Every
      receptor is reported INCLUDING the ones that fail — a transboundary claim that only works
      for Singapore is a finding about Singapore, and saying so is worth more than an average
      that hides it.

G-J5  Anchor-event replay.  2015 and 2019 are excluded from training entirely.  The model,
      scored blind, must place both in the top decile of national seasonal severity across the
      record.  Note that 2015 has NO Singapore ground truth — the NEA history begins 2016-03 —
      so the 2015 severity reference is FIRMS detections plus CAMS EAC4, and the gate says which.
      If the model cannot rank the two crises everyone remembers, nothing else here is worth
      reading.

OUTPUT: data/stats.json — every gate with its threshold, its computed value, pass/fail, and a
one-line diagnosis when it fails.  The dashboard renders this table verbatim.
"""

from __future__ import annotations

import config
import util
from util import log


def g_j1_hotspot_hygiene():
    raise NotImplementedError


def g_j2_risk_skill():
    raise NotImplementedError


def g_j3_transport_direction():
    raise NotImplementedError


def g_j4_receptor_correlation():
    raise NotImplementedError


def g_j5_anchor_replay():
    raise NotImplementedError


def main() -> None:
    log("validate: not implemented (scaffold) — gates", ", ".join(config.GATES))
    util.guard_disk()


if __name__ == "__main__":
    main()
