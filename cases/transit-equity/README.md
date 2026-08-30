# Case G — Transit Access & Urban Equity (Phase 3)

TransJakarta (240 routes incl. 98 Mikrotrans — feed unpacked and verified
2026-08-30), KRL, MRT and both LRTs on one r5py routing graph with the OSM
street/footpath network: 60/45/30-minute access to jobs and healthcare for
~1,500 Jabodetabek kelurahan, then the equity read — Lorenz/Gini/Palma and
access-vs-poverty via Case F's estimates.

Status: **SCAFFOLDED — spec written, data path verified 2026-08-30 (GTFS
downloaded and inspected), pipeline stubs in place, no heavy runs yet.**
Spec (governing document): `docs/spec-transit-equity.html`.

## Non-negotiables from the spec

- **Scheduled times, not congestion** — weekday 07:00–09:00 departure window,
  frequency-aware (RAPTOR); stated on every view.
- **Hand-encoded rail is labelled** — no GTFS exists for KRL/MRT/LRT (verified
  against Transitland + Mobility Database); headways come from cited official
  pages and every rail time carries a ±15 % caveat.
- **Gates**: published-timetable OD sample within ±15 %/±8 min (G-G1, hard);
  Google Routes comparison MAD ≤ 10 min on 50 OD pairs, on-the-fly only,
  never stored, never mapped (G-G2, Maps ToS §19); stop-snapping ≥ 98 % within
  200 m and zero unreachable origins (G-G3, hard); monotonicity + ITDP
  People-Near-Transit replication above the 2016 anchors (G-G4).
- **Jobs are a proxy** — GHS-BUILT-S NRES floorspace + OSM POI density, named
  as such ("job-dense floorspace"), never "jobs".

## Run (stubs for now; needs JDK 21 for r5py)

```sh
uv sync
make rebuild               # ingest → rail → network → matrix → access → equity → validate → export
make validate              # gates G-G1..G-G4
```

`web/` is the case's Astro app (port 4329, base `/transit`,
`data-case="transit"` tokens — metro-line chromatics on graphite).

## Decisions pending user verification

1. **TransJakarta GTFS licence is unstated** (feed, Transitland, Mobility
   Database all silent). Proceeding with attribution + a written-confirmation
   request, per the build-plan risk register. If refused, the case loses its
   richest layer.
2. **Meta RWI rejected (CC BY-NC)** → the equity axis uses Case F's kecamatan
   poverty estimates instead, creating a build-order dependency **F before G**.
3. **Hand-encoded frequency GTFS for rail** as publicly-defensible methodology
   (headways re-read from official pages at build time, ±15 % caveat shown).
4. **Google Routes as validation-only comparator** inside the free Essentials
   tier (10,000 calls/mo) and ToS §19 (30-day cache ceiling, no display on
   non-Google maps).
5. **Kepulauan Seribu**: include in maps but exclude from equity aggregates
   (boat-only access distorts Gini)?
