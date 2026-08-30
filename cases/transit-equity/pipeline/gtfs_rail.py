"""Stage 2 · rail — hand-encoded frequency GTFS for the modes that publish no feed.

No official GTFS exists for KRL Commuter (KAI Commuter), MRT Jakarta, LRT Jakarta or LRT
Jabodebek (Transitland + Mobility Database, 2026-08-30 — spec §G2). This stage builds one
frequency-based GTFS from:

  stations   OSM route relations (route=train|subway|light_rail) in the Jabodetabek bbox,
             via Overpass; stop order = the relation's member order (PTv2 lists stops in
             travel order); coordinates from OSM railway=station|halt nodes.
  run times  published end-to-end journey times → an average commercial speed per line
             (config.RAIL_SPEED_KMH, each with its source), distributed by inter-station
             great-circle distance, plus a fixed dwell (config.RAIL_DWELL_S).
  headways   published peak/off-peak headways (config.RAIL_LINES, each with its source URL
             and the date it was read) → frequencies.txt.

EVERYTHING HERE IS HAND-ENCODED and is labelled as such on the dashboard; rail travel times
carry a ±15 % caveat until an operator publishes a feed. Sources are echoed into
data/gtfs/rail_sources.json for the methodology chapter.

Output: data/gtfs/rail_handencoded.zip (+ rail_sources.json).
"""

from __future__ import annotations

import io
import json
import math
import re
import zipfile
from pathlib import Path

import config
import util
from util import log

OVERPASS_CACHE = config.RAW / "osm" / "rail_overpass.json"
SOURCES_JSON = config.GTFS_DIR / "rail_sources.json"

# The member stops of these relations are public_transport=stop_position nodes, not
# railway=station nodes, so the member nodes themselves are recursed (node(r.r)) — asking
# only for railway=station returned relations whose members were all unknown.
QUERY = """
[out:json][timeout:300];
(
  rel["type"="route"]["route"="subway"]({s},{w},{n},{e});
  rel["type"="route"]["route"="light_rail"]({s},{w},{n},{e});
  rel["type"="route"]["route"="train"]["name"~"Commuter|KRL|Lin |Line",i]({s},{w},{n},{e});
)->.r;
.r out body;
node(r.r);
out body;
"""


def overpass() -> dict:
    if OVERPASS_CACHE.exists():
        return json.loads(OVERPASS_CACHE.read_text())
    import requests
    w, s, e, n = config.BBOX
    q = QUERY.format(w=w, s=s, e=e, n=n)
    log("Overpass: rail relations + station nodes")
    for attempt in range(3):
        # Overpass answers 406 to the browser UA used for the portals; identify honestly here.
        r = requests.post(config.OVERPASS_URL, data={"data": q},
                          headers={"User-Agent": "demo-lab-transit-equity/1.0"}, timeout=420)
        if r.status_code == 200:
            OVERPASS_CACHE.parent.mkdir(parents=True, exist_ok=True)
            OVERPASS_CACHE.write_text(r.text)
            util.manifest_put("osm_rail_overpass", source=config.OVERPASS_URL,
                              licence="ODbL 1.0", bytes=len(r.text))
            return r.json()
        log("overpass", r.status_code, "retrying")
    raise RuntimeError("Overpass unavailable")


def haversine(a, b) -> float:
    (lat1, lon1), (lat2, lon2) = a, b
    p = math.pi / 180
    h = (0.5 - math.cos((lat2 - lat1) * p) / 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2)
    return 12742000 * math.asin(math.sqrt(max(h, 0.0)))


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).strip()


def pick_relations(data: dict) -> dict:
    """One relation per config line: right route type, name matching, most stations."""
    els = data["elements"]
    nodes = {el["id"]: el for el in els if el["type"] == "node"}
    rels = [el for el in els if el["type"] == "relation"]
    log(f"overpass: {len(rels)} route relations, {len(nodes)} station nodes")
    out = {}
    for key, (route, rx) in config.RAIL_OSM_MATCH.items():
        best, best_stops = None, []
        for rel in rels:
            t = rel.get("tags", {})
            if t.get("route") != route:
                continue
            hay = norm(" ".join(str(t.get(k, "")) for k in ("name", "ref", "from", "to", "via", "operator")))
            if not re.search(rx, hay):
                continue
            stops = []
            seen = set()
            for m in rel.get("members", []):
                if m["type"] != "node" or m["ref"] not in nodes:
                    continue
                nd = nodes[m["ref"]]
                nm = nd.get("tags", {}).get("name")
                if not nm or norm(nm) in seen:
                    continue
                seen.add(norm(nm))
                stops.append({"name": nm, "lat": nd["lat"], "lon": nd["lon"], "osm_id": nd["id"]})
            if len(stops) > len(best_stops):
                best, best_stops = rel, stops
        if best is None or len(best_stops) < 3:
            log(f"  ! {key}: no usable relation (best {len(best_stops)} stops) — line skipped")
            continue
        out[key] = {"relation_id": best["id"], "relation_name": best.get("tags", {}).get("name", ""),
                    "stops": best_stops}
        log(f"  {key}: rel {best['id']} '{out[key]['relation_name'][:48]}' — {len(best_stops)} stations")
    return out


def build_gtfs(lines: dict) -> None:
    files: dict[str, list[str]] = {
        "agency.txt": ["agency_id,agency_name,agency_url,agency_timezone"],
        "stops.txt": ["stop_id,stop_name,stop_lat,stop_lon"],
        "routes.txt": ["route_id,agency_id,route_short_name,route_long_name,route_type,route_color"],
        "trips.txt": ["route_id,service_id,trip_id,trip_headsign,direction_id"],
        "stop_times.txt": ["trip_id,arrival_time,departure_time,stop_id,stop_sequence"],
        "frequencies.txt": ["trip_id,start_time,end_time,headway_secs,exact_times"],
        "calendar.txt": ["service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date"],
        "transfers.txt": ["from_stop_id,to_stop_id,transfer_type,min_transfer_time"],
    }
    files["calendar.txt"].append("WEEKDAY,1,1,1,1,1,1,1,20250101,20271231")
    agencies = set()
    colors = {"train": "3E8EDE", "subway": "57B26A", "light_rail": "E0A63F"}
    stop_index: dict[str, list[tuple[str, float, float]]] = {}

    for key, got in lines.items():
        meta = config.RAIL_LINES[key]
        route = config.RAIL_OSM_MATCH[key][0]
        rtype = config.RAIL_ROUTE_TYPE[route]
        agency = meta["operator"]
        aid = re.sub(r"[^A-Za-z0-9]", "", agency)
        if aid not in agencies:
            agencies.add(aid)
            files["agency.txt"].append(f'{aid},"{agency}",https://example.invalid,Asia/Jakarta')
        rid = f"rail_{key}"
        files["routes.txt"].append(
            f'{rid},{aid},"{key.replace("_", " ").title()}","{got["relation_name"] or key}",{rtype},{colors[route]}')

        stops = got["stops"]
        speed = config.RAIL_SPEED_KMH[key] * 1000 / 3600.0     # m/s, commercial (incl. dwell)
        for i, st in enumerate(stops):
            sid = f"{rid}_{i}"
            st["stop_id"] = sid
            name = st["name"].replace('"', "")
            files["stops.txt"].append(f'{sid},"{name}",{st["lat"]:.6f},{st["lon"]:.6f}')
            stop_index.setdefault(norm(name), []).append((sid, st["lat"], st["lon"]))

        for direction in (0, 1):
            seq = stops if direction == 0 else list(reversed(stops))
            tid = f"{rid}_d{direction}"
            files["trips.txt"].append(f'{rid},WEEKDAY,{tid},"{seq[-1]["name"]}",{direction}')
            t = 6 * 3600                                        # nominal trip start (frequencies drive service)
            for i, st in enumerate(seq):
                if i:
                    d = haversine((seq[i - 1]["lat"], seq[i - 1]["lon"]), (st["lat"], st["lon"]))
                    t += max(60, int(d / speed)) + config.RAIL_DWELL_S
                hhmmss = f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"
                files["stop_times.txt"].append(f"{tid},{hhmmss},{hhmmss},{st['stop_id']},{i + 1}")
            pk = int(float(meta["headway_peak_min"]) * 60)
            off = int(float(meta["headway_off_min"]) * 60)
            files["frequencies.txt"] += [
                f"{tid},05:00:00,09:00:00,{pk},0",
                f"{tid},09:00:00,16:00:00,{off},0",
                f"{tid},16:00:00,20:00:00,{pk},0",
                f"{tid},20:00:00,23:30:00,{off},0",
            ]

    # same-named stations on different lines → an explicit interchange (R5 also transfers via
    # the street network; this makes the named hubs certain).
    for name, group in stop_index.items():
        if len(group) < 2:
            continue
        for a in group:
            for b in group:
                if a[0] != b[0] and haversine((a[1], a[2]), (b[1], b[2])) < 600:
                    files["transfers.txt"].append(f"{a[0]},{b[0]},2,240")

    config.RAIL_GTFS.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(config.RAIL_GTFS, "w", zipfile.ZIP_DEFLATED) as z:
        for name, rows in files.items():
            z.writestr(name, "\n".join(rows) + "\n")
    n_stops = len(files["stops.txt"]) - 1
    log("rail GTFS", config.RAIL_GTFS.name, f"{len(lines)} lines, {n_stops} stations,",
        f"{len(files['transfers.txt']) - 1} interchange pairs")

    SOURCES_JSON.write_text(json.dumps({
        "note": ("Hand-encoded frequency GTFS: no operator publishes a feed for KRL/MRT/LRT. "
                 "Headways and end-to-end times are read from the operators' published pages; "
                 "inter-station run times are distributed by distance at the line's average "
                 "commercial speed. Rail travel times carry a ±15 % caveat."),
        "lines": {k: {"operator": config.RAIL_LINES[k]["operator"],
                      "headway_peak_min": config.RAIL_LINES[k]["headway_peak_min"],
                      "headway_off_min": config.RAIL_LINES[k]["headway_off_min"],
                      "avg_commercial_kmh": config.RAIL_SPEED_KMH[k],
                      "headway_source": config.RAIL_LINES[k]["source"],
                      "stations": len(v["stops"]),
                      "osm_relation": v["relation_id"], "osm_relation_name": v["relation_name"]}
                  for k, v in lines.items()},
        "geometry_licence": "OSM contributors, ODbL 1.0",
        "caveat_pct": config.RAIL_TT_CAVEAT_PCT,
    }, indent=2))


def main() -> None:
    util.guard_disk()
    lines = pick_relations(overpass())
    if not lines:
        raise SystemExit("no rail lines resolved from OSM — cannot build rail GTFS")
    build_gtfs(lines)


if __name__ == "__main__":
    main()
