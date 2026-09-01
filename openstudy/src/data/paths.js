// The ways in, routed by what someone already has rather than by what they call
// themselves. A quantitative economist is a researcher and a data scientist both; asking
// people to pick an identity sorts them into boxes none of them fit, and it contradicts
// CRediT, which exists to credit contributions rather than titles.
//
// `status` is capacity, stated honestly. One person cannot service five doors, and a door
// marked open that answers in three weeks costs more trust than one marked "case by case".
// Shared between the homepage router and /contribute/ so the two cannot disagree.
//
//   "open"          published as available now
//   "case by case"  real, has happened, but rationed by review time — ask first

import { SEEKING } from "./instruments.js";

export const PATHS = [
  {
    anchor: "published",
    have: "A published paper, and the data behind it",
    path: "An instrument built on it",
    status: "open",
    note: "No preprint risk, nothing to negotiate, and something you can present. The research is already refereed.",
  },
  {
    anchor: "interpret",
    have: "A subject you know, and no instrument",
    path: `Interpret one of the ${SEEKING.length} waiting`,
    status: "open",
    note: "No data to find, no code to write. Write the conclusion, take first author.",
  },
  {
    anchor: "new",
    have: "A question and a data source, no analysis yet",
    path: "A new study, gated before anything is built",
    status: "open",
    note: "Question, source and thresholds in writing first. Then it gets built.",
  },
  {
    anchor: "attack",
    have: "Skills and time, no particular subject",
    path: "Replicate or break an existing instrument",
    status: "open",
    note: "The most valuable thing per hour spent here, and the fastest way to be useful.",
  },
  {
    anchor: "simulation",
    have: "Simulation or model output nobody outside your group can open",
    path: "A browser-native visualization of it",
    status: "case by case",
    note: "Built to the visualization standard, with no gate claims — there is nothing here to validate.",
  },
  {
    anchor: "bring",
    have: "An instrument you built, and no domain author",
    path: "Bring it, with a named target",
    status: "case by case",
    note: `There are already ${SEEKING.length} without one — so bring someone who wants to interpret it.`,
  },
];

/** "five doors" / "six doors" — the router prose reads this instead of a written number. */
export const DOOR_WORD = ["no", "one", "two", "three", "four", "five", "six", "seven"];

export const OPEN_PATHS = PATHS.filter((p) => p.status === "open");
export const ASK_FIRST = PATHS.filter((p) => p.status !== "open");

/** "4 ways in are open, and one more on request" — so no page hardcodes either count. */
export function waysPhrase() {
  const words = ["no", "one", "two", "three", "four", "five", "six"];
  const open = OPEN_PATHS.length;
  const ask = ASK_FIRST.length;
  if (!ask) return `All ${words[open] ?? open} ways in are open`;
  return `${open} ways in are open, and ${words[ask] ?? ask} more on request`;
}
