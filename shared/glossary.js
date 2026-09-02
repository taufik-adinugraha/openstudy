/**
 * One definition per term, for the whole site.
 *
 * Written for someone who knows their own subject and not this vocabulary — an economist
 * who studies food security has no reason to know what backscatter is, and asking her to
 * learn it before she can read a claim about rice is the wrong way round.
 *
 * Rules for entries, so they stay useful:
 *   - one or two sentences, no formulae, no other jargon inside a definition
 *   - say what it is FOR, not just what it is
 *   - where a term is routinely misread, say what it does not mean
 *
 * Articles render only the terms they actually use (see glossaryFor), so this file is the
 * single source of truth and no page can drift from it.
 */

export const GLOSSARY = {
  // ── instruments and data sources ────────────────────────────────────────
  "Sentinel-1": {
    term: "Sentinel-1",
    plain: "A European radar satellite. Because radar makes its own signal it sees through cloud and at night, which matters in a country that is cloudy for half the year.",
  },
  "Sentinel-5P": {
    term: "Sentinel-5P",
    plain: "A European satellite that measures gases in the atmosphere rather than surfaces — used for air quality rather than land.",
  },
  "Black Marble": {
    term: "Black Marble",
    plain: "NASA's processed night-lights product: how much light each patch of ground emits at night, cleaned of moonlight and cloud. Used as an indirect signal of economic activity.",
  },
  VIIRS: {
    term: "VIIRS",
    plain: "The instrument on the satellites that records night lights. Different versions of the same instrument are not interchangeable, which is why the year matters more than the brightness.",
  },
  radar: {
    term: "radar",
    plain: "Instead of photographing reflected sunlight, the satellite sends its own microwave pulse and listens for the echo. It works through cloud and at night; it does not see colour.",
  },
  backscatter: {
    term: "backscatter",
    plain: "How much of a radar pulse bounces back. Smooth wet surfaces return little, rough vegetation returns more — which is how flooded rice fields can be told from dry ones.",
  },
  InSAR: {
    term: "InSAR",
    plain: "Comparing radar images of the same place taken weeks apart, precisely enough to measure ground that has sunk by millimetres.",
  },
  GDELT: {
    term: "GDELT",
    plain: "A public archive of news coverage worldwide, machine-read for who and what is mentioned. It records what was reported, not what happened.",
  },
  ERA5: {
    term: "ERA5",
    plain: "A reconstructed record of past weather — a model reconciled with observations to give a consistent history of wind, rain and temperature.",
  },
  GTFS: {
    term: "GTFS",
    plain: "The standard file format transport operators publish timetables in. It says when a bus is scheduled, not when it arrives.",
  },
  FWI: {
    term: "Fire Weather Index",
    plain: "A long-established weather-based measure of how easily fires start and spread. Any new fire model has to beat it to be worth having.",
  },

  // ── what a radar measurement actually is ────────────────────────────────
  VV: {
    term: "VV and VH",
    plain: "Two ways the radar listens. VV sends and receives the same way up and responds mostly to how wet and smooth the ground is — a flooded field goes dark. VH listens the other way up and responds to structure, so it rises as a canopy grows. Reading the two together is how a flooded paddy can be told from a growing crop.",
  },
  "γ⁰": {
    term: "γ⁰ (gamma nought)",
    plain: "The standardised strength of the returned radar signal, corrected for terrain so a hillside is comparable with a plain. It is what \"how bright the radar sees this field\" means numerically.",
  },
  look: {
    term: "a look",
    plain: "One pass of the satellite over a field. More looks means the crop's cycle is sampled more often; too few and a short stage — flooding, heading — can happen entirely between two looks and never be seen.",
  },
  revisit: {
    term: "revisit",
    plain: "How many days pass between looks at the same field. It sets the finest thing the method can detect: a 12-day revisit cannot resolve a stage that lasts a week.",
  },
  "C-band": {
    term: "C-band and X-band",
    plain: "Radar wavelengths. C-band (Sentinel-1) penetrates a canopy further and suits whole-field crop monitoring; X-band is shorter, sees finer structure, and behaves differently over the same rice — which is why results from the two are not interchangeable.",
  },
  "incidence angle": {
    term: "incidence angle",
    plain: "How steeply the radar looks down at the ground. The same field returns a different value at a different angle, so passes from different orbits cannot be compared until this is corrected.",
  },
  F1: {
    term: "F1 score",
    plain: "One number combining precision and recall, for when a model must both find the real cases and avoid false alarms. It hides which of the two is weak, so it is reported alongside them rather than instead.",
  },

  // ── models ──────────────────────────────────────────────────────────────
  "gradient boosting": {
    term: "gradient boosting",
    plain: "A machine-learning method that builds many small, deliberately weak rules and adds them up. Strong at prediction; it does not explain why, and it cannot see anything absent from its inputs.",
  },
  "random forest": {
    term: "random forest",
    plain: "A machine-learning method that averages many decision trees grown on different slices of the data, which makes it steadier than any single tree.",
  },
  "quantile regression": {
    term: "quantile regression",
    plain: "Instead of predicting one number, it predicts a range — for example the 10th and 90th percentile — so the uncertainty is part of the answer.",
  },
  "small-area estimation": {
    term: "small-area estimation",
    plain: "Producing estimates for places too small for the survey to measure directly, by borrowing strength from similar places. The estimate is always less certain than the survey it came from.",
  },
  nowcast: {
    term: "nowcast",
    plain: "An estimate of what a statistic is doing right now, before the official figure is published. It is a forecast of the present, not the future.",
  },
  deseason: {
    term: "deseasonalise",
    plain: "Removing the regular yearly pattern — the monsoon, the harvest, Ramadan — so what is left is the change that is not just the calendar.",
  },
  elasticity: {
    term: "elasticity",
    plain: "How much one thing moves when another moves by one percent. An elasticity near zero means the two barely track each other.",
  },

  // ── how a model is judged ───────────────────────────────────────────────
  baseline: {
    term: "baseline",
    plain: "The dumbest sensible answer, used as the thing to beat — for example \"this year looks like last year\". A model that cannot beat its baseline has added nothing, however good its score looks alone. Careful: in the radar sections the same word means something else entirely — a reference level a signal is measured against, as in \"3 dB below the cell's own baseline\".",
  },
  "hold-out": {
    term: "hold-out",
    plain: "Data deliberately kept away from the model while it learns, then used to test it. Without one, a model is graded on the answers it was shown.",
  },
  "cross-validation": {
    term: "cross-validation",
    plain: "Testing repeatedly, each time holding back a different slice of the data, so the score does not depend on one lucky split.",
  },
  "leave-one-province-out": {
    term: "leave-one-province-out",
    plain: "A strict test: the model never sees the province it is being scored on. Neighbouring places resemble each other, so an ordinary random split quietly lets the answer leak in and flatters the model.",
  },
  fold: {
    term: "fold",
    plain: "One slice of the data in a repeated test. How the slices are drawn can matter more to the score than the model does.",
  },
  leakage: {
    term: "leakage",
    plain: "When information the model should not have had reaches it during training — a neighbour, a later date, the answer itself. It produces good scores and useless predictions.",
  },
  "R²": {
    term: "R² (r-squared)",
    plain: "How much of the variation the model accounts for. 1 is perfect, 0 is no better than guessing the average, and below 0 is worse than guessing the average — which does happen.",
  },
  RMSE: {
    term: "RMSE",
    plain: "Typical size of the error, in the units of the thing being measured, with big misses weighted heavily.",
  },
  MAE: {
    term: "MAE",
    plain: "Average size of the error, in the units of the thing being measured, treating all misses alike.",
  },
  AUC: {
    term: "AUC",
    plain: "For yes/no predictions: the chance the model ranks a real case above a non-case. 0.5 is a coin toss. It flatters models when the thing being predicted is rare.",
  },
  ROC: {
    term: "ROC",
    plain: "The curve behind AUC, trading off catching real cases against raising false alarms.",
  },
  precision: {
    term: "precision",
    plain: "Of the cases the model flagged, how many were real. It can always be raised by flagging fewer things, which is why it is never reported alone.",
  },
  recall: {
    term: "recall",
    plain: "Of the real cases, how many the model found. Precision and recall pull against each other.",
  },
  Brier: {
    term: "Brier score",
    plain: "Scores a probability rather than a yes/no — it rewards being confident when right and punishes being confident when wrong. Lower is better.",
  },
  Spearman: {
    term: "Spearman correlation",
    plain: "Whether two lists put things in the same order, ignoring the actual values. Useful when the ranking matters more than the number.",
  },
  residual: {
    term: "residual",
    plain: "What the model got wrong at one place or time — the observed value minus the predicted one. Patterns in the residuals show what the model cannot see.",
  },
  intercept: {
    term: "intercept",
    plain: "The part of a prediction that is the same for everything, before any input is considered. A prediction that is mostly intercept is mostly a constant wearing a model's clothes.",
  },

  // ── inequality and economics ────────────────────────────────────────────
  Gini: {
    term: "Gini coefficient",
    plain: "One number for how unequally something is spread: 0 is everyone equal, 1 is one person holding it all.",
  },
  Palma: {
    term: "Palma ratio",
    plain: "What the richest tenth has, divided by what the poorest 40% has. More sensitive to the two ends than the Gini.",
  },
  Pareto: {
    term: "Pareto front",
    plain: "The set of options where you cannot improve one goal without giving up another — cheaper but less efficient, or the reverse. It is a menu of trade-offs, not a single best answer.",
  },
  exergy: {
    term: "exergy",
    plain: "The useful part of energy — how much work it could actually do, given the surrounding temperature. Efficiency measured this way exposes waste that ordinary efficiency hides.",
  },
  isentropic: {
    term: "isentropic efficiency",
    plain: "How close a real compressor comes to the ideal, lossless one. Around 0.65 means a third of the effort goes to heat and friction rather than compression.",
  },

  // ── statistics a reader meets in passing ────────────────────────────────
  calibration: {
    term: "calibration",
    plain: "Adjusting a model's output so it lines up with a trusted measurement. It can genuinely fix a scale error — or quietly force agreement and teach you nothing, which is why the before-and-after is always published here.",
  },
  percentile: {
    term: "percentile",
    plain: "The value below which a given share of cases fall. The 90th percentile is the level only one case in ten exceeds.",
  },
  median: {
    term: "median",
    plain: "The middle value: half are higher, half lower. Unlike an average, one extreme case cannot drag it.",
  },
  interquartile: {
    term: "interquartile range",
    plain: "The span covering the middle half of the cases, ignoring the extremes at both ends.",
  },
  "confidence interval": {
    term: "confidence interval",
    plain: "A range the true value is likely to sit in, given how much data there is. A wide interval is not a failure — it is the honest width of what the data supports.",
  },
  bootstrap: {
    term: "bootstrap",
    plain: "Re-drawing the same data thousands of times to see how much an answer wobbles. It turns one estimate into a range without assuming the shape of the errors.",
  },
  autocorrelation: {
    term: "autocorrelation",
    plain: "When nearby measurements resemble each other — yesterday's weather, the next village. It makes ordinary statistics overconfident, because near-duplicates are counted as independent evidence.",
  },
  sensitivity: {
    term: "sensitivity (analysis)",
    plain: "Changing an assumption on purpose to see how much the answer moves. If a conclusion depends on a choice nobody can justify, this is what exposes it.",
  },
  log1p: {
    term: "log transform",
    plain: "Compressing a scale so that large values stop dominating. It helps a model fit, and it also drags predictions toward the middle — which matters when the extremes are the thing you cared about.",
  },

  // ── remote sensing, in passing ──────────────────────────────────────────
  composite: {
    term: "composite",
    plain: "One image assembled from many passes over the same place, so cloud and noise average out. Composites built by different methods are not comparable, even from the same satellite.",
  },
  raster: {
    term: "raster",
    plain: "A grid of cells covering the ground, each holding one measurement. The cell size sets the finest thing the data can possibly show.",
  },
  orbit: {
    term: "orbit (ascending/descending)",
    plain: "Whether the satellite passed heading north or south. It looks from a different angle each way, so measurements from the two are never mixed.",
  },
  dB: {
    term: "decibel (dB)",
    plain: "A ratio on a compressed scale, used for radar strength. Because it is compressed, averaging decibels is not the same as averaging the underlying signal — and doing it the wrong way biases whole regions.",
  },

  // ── Indonesian institutions and surveys ─────────────────────────────────
  BPS: {
    term: "BPS",
    plain: "Badan Pusat Statistik, Indonesia's national statistics agency. Its published figures are the benchmark every case here is scored against.",
  },
  BMKG: {
    term: "BMKG",
    plain: "Indonesia's meteorology, climatology and geophysics agency — the official source for weather and earthquakes.",
  },
  BNPB: {
    term: "BNPB",
    plain: "Indonesia's national disaster management agency, which acts on flood, fire and earthquake information.",
  },
  Susenas: {
    term: "Susenas",
    plain: "Indonesia's national socio-economic household survey. It is what the official poverty rate is measured from, and its sample size sets the smallest area that figure can honestly describe.",
  },
  KSA: {
    term: "KSA",
    plain: "BPS's area sampling framework for rice — field observers reporting crop stage on sampled plots. It is the official harvest figure a satellite estimate has to answer to.",
  },
  GRDP: {
    term: "GRDP / PDRB",
    plain: "Gross regional domestic product: the economic output of one province or regency. Published annually, long after the year it describes, which is why a faster estimate is wanted.",
  },

  // ── terms the checker found in published prose, undefined ───────────────
  Bayesian: {
    term: "Bayesian",
    plain: "An approach that starts from what was already believed and updates it with new evidence, ending with a range of plausible answers rather than a single number.",
  },
  ensemble: {
    term: "ensemble",
    plain: "Running the same model many times with slightly different starting points, and reading the spread of results as the uncertainty.",
  },
  coherence: {
    term: "coherence",
    plain: "How stable a radar echo stays between two passes. High coherence means the ground has not changed much, which is what makes measuring millimetres of movement possible; vegetation destroys it.",
  },
  speckle: {
    term: "speckle",
    plain: "The grainy noise in every radar image, as strong as the signal itself in a single pixel. It is why radar measurements are averaged over an area before they mean anything.",
  },
  zonal: {
    term: "zonal statistics",
    plain: "Summarising a grid of measurements inside a boundary — the average brightness within one regency, say. The boundary version matters: districts split over time, so the same name can mean different areas in different years.",
  },
  pemekaran: {
    term: "pemekaran",
    plain: "The splitting of an Indonesian district or province into new ones. It means a place's boundary changed, so a rise in any total may be an administrative event rather than a real one.",
  },
  Podes: {
    term: "Podes",
    plain: "Indonesia's village potential census — a periodic count of facilities and conditions in every village. It reaches below the level household surveys can describe.",
  },
  NDVI: {
    term: "NDVI",
    plain: "A greenness index from satellite imagery, high where leaves are dense. It saturates: beyond a certain thickness of canopy it stops distinguishing more growth.",
  },
  "fixed effects": {
    term: "fixed effects",
    plain: "Letting every place or year have its own baseline level, so the model compares within a place over time rather than between places. It removes differences it cannot see, and also removes the ability to say anything about them.",
  },

  // ── Indonesian administrative units ─────────────────────────────────────
  kabupaten: {
    term: "kabupaten",
    plain: "An Indonesian regency — the administrative level below a province, and the level most official statistics are published at.",
  },
  kecamatan: {
    term: "kecamatan",
    plain: "An Indonesian sub-district, below a regency. Official poverty figures are not published at this level, which is why estimating them there is both useful and risky.",
  },
  concession: {
    term: "concession",
    plain: "A licensed area a company may operate in — for palm, timber or mining. Who holds which concession is often not public, which limits what can be attributed to whom.",
  },
  peat: {
    term: "peatland",
    plain: "Waterlogged ground made of partly decayed plants, holding enormous amounts of carbon. Drained peat burns underground for weeks and is very hard to extinguish.",
  },
};

/** Terms this text actually uses, in the order they are defined. */
export function glossaryFor(text) {
  const hay = String(text);
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return Object.values(GLOSSARY).filter((e) => {
    const keys = Object.keys(GLOSSARY).filter((k) => GLOSSARY[k] === e);
    // Word boundaries, or "fold" matches "folder" and "manifold" and every article
    // ends up claiming every term.
    return [e.term, ...keys].some((n) => {
      // Not \\b: that fails after a superscript, so "R²" never matched. Require only
      // that the term is not glued to a letter on either side.
      const w = esc(String(n));
      return new RegExp(`(?<![A-Za-z])${w}(?![A-Za-z])`, "i").test(hay);
    });
  });
}

/**
 * Terms that must be defined if a reader meets them.
 *
 * ops/review reads this and fails a case whose PUBLISHED prose uses one of these while
 * GLOSSARY does not define it. It reads the rendered page rather than the source,
 * because source scanning flags variable names — "F1" appeared in nine articles as
 * `const F1 = fig1()` and in none of them as a word a reader sees.
 *
 * Add a term here the moment it enters an article; the checker then insists on a
 * definition rather than trusting anyone to remember.
 */
export const WATCHLIST = [
  "speckle", "coherence", "multi-look", "incidence angle", "pemekaran", "zonal",
  "heteroskedastic", "collinearity", "regularisation", "hyperparameter", "SHAP",
  "confusion matrix", "specificity", "p-value", "OLS", "fixed effects",
  "difference-in-differences", "propensity", "Fay-Herriot", "Podes", "PMTiles",
  "kriging", "variogram", "endmember", "NDVI", "albedo", "aerosol optical depth",
  "reanalysis", "Monte Carlo", "prior distribution", "posterior distribution",
];

export const TERM_COUNT = Object.keys(GLOSSARY).length;
