// The ten instruments, and what each one is missing.
//
// `gap` is the invitation. It is written from what the instrument actually found —
// a generic "get in touch" gets no replies, and a reader can tell the difference.
// `status` is "open" until a domain author writes the interpretation.

export const BASE = "https://demolab-id.duckdns.org";

export const INSTRUMENTS = [
  {
    slug: "nightlights",
    title: "Nighttime-Lights Economic Pulse",
    line: "Can satellite lights stand in for regional economic activity in Indonesia?",
    found: "The nowcast is 99.5% intercept and 41% worse than carrying last year forward. The Gibson relationship holds for kota and inverts for kabupaten.",
    gap: "Someone who knows Indonesian regional economic statistics — why it inverts, whether BPS's GRDP methodology explains it, and what lights can honestly proxy in a country with this settlement pattern.",
    status: "open",
  },
  {
    slug: "airquality",
    title: "Jabodetabek Air Quality Nowcast",
    line: "How bad will the air be tomorrow, when almost nothing is measuring it?",
    found: "Only 2 of 24 registered public PM2.5 sensors still report. Two US diplomatic stations last published 3,580 days ago.",
    gap: "Someone who knows Indonesian air-quality monitoring — why the network collapsed, what BMKG's own record would add, and what a 24-hour forecast is actually useful for here.",
    status: "open",
  },
  {
    slug: "haze",
    title: "Fire & Haze Early Warning",
    line: "Can an ignition model beat the Fire Weather Index over Indonesian peat?",
    found: "It clears both climatology and the FWI at every lead. Two gates fail: transport direction, and the 2015/2019 anchor replay.",
    gap: "Someone who knows Indonesian fire management and peat hydrology — whether those failures matter operationally, what BNPB and KLHK act on, and whether those years are the right blind test.",
    status: "open",
  },
  {
    slug: "forest",
    title: "Forest & Commodity Watch",
    line: "Does a mill catchment tell you anything about who cleared the forest?",
    found: "Three-quarters of alerted hectares fall inside a mill sourcing catchment — but the base-rate test asks what that share would be by chance.",
    gap: "Someone who knows palm supply chains and Indonesian concession law — whether catchment proximity means anything for sourcing responsibility, and what ISPO and RSPO actually require.",
    status: "open",
  },
  {
    slug: "rice",
    title: "Rice & Food Security",
    line: "Reading Java's harvest from radar, and checking it against the official count.",
    found: "Detected harvested area has an R² of −11.09 against BPS KSA before calibration, and 0.82 after.",
    gap: "Someone in agricultural statistics or rice agronomy — whether that calibration is legitimate or is fitting the answer, what KSA's own method assumes, and whether harvest-timing prediction is actionable.",
    status: "open",
  },
  {
    slug: "jakarta",
    title: "Jakarta Is Sinking",
    line: "How fast, where, and what the ground is doing about it.",
    found: "Vertical land motion across Java, with the fastest kelurahan named and an independent InSAR run as a check.",
    gap: "Someone in geodesy or hydrogeology — how much is groundwater extraction against natural compaction and load, what the coastal defence programme assumes, and which measurements would settle it.",
    status: "open",
  },
  {
    slug: "poverty",
    title: "Poverty Mapping from Space",
    line: "Downscaling poverty estimates below the level the survey supports.",
    found: "A gradient-boosted model with spatial cross-validation, taken down to kecamatan.",
    gap: "Someone who knows BPS poverty measurement — whether estimates at that resolution can responsibly be used for targeting, what Susenas sampling supports, and where a sub-district figure would do harm.",
    status: "open",
  },
  {
    slug: "transit",
    title: "The Hour",
    line: "What can you reach in an hour, and who cannot reach anything?",
    found: "Access routed on published timetables. Two hard gates fail: timetable sanity and network integrity.",
    gap: "Someone in Jabodetabek transport planning — whether scheduled-not-congested times are usable, what the missing angkot network does to the equity picture, and what planners would actually ask this.",
    status: "open",
  },
  {
    slug: "trade",
    title: "Indonesia in the Global Trade Network",
    line: "Where the economy sits in the product space, and where it is moving.",
    found: "Economic complexity computed from the bilateral trade record, tracked over time.",
    gap: "Someone in trade economics or industrial policy — what complexity does and does not predict for a commodity exporter, and whether the product-space frame fits Indonesia's actual position.",
    status: "open",
  },
  {
    slug: "narrative",
    title: "Indonesia in the Global Narrative",
    line: "What the world's press reported about Indonesia, against its own denominator.",
    found: "Every GDELT record touching Indonesia since 2017, counted against a share-of-everything baseline rather than in isolation.",
    gap: "Someone who studies Indonesian media — what GDELT's Anglophone and online bias does to that picture, whether machine-coded tone survives translation, and what a claim about national narrative can rest on.",
    status: "open",
  },
];

export const OPEN_COUNT = INSTRUMENTS.filter((i) => i.status === "open").length;
