// How regularly the maps were updated: the start times of the recent runs of
// update.yml, written by the workflow (gh run list). Optional at build time.
type Run = { createdAt: string; status: string; conclusion: string };

const runs = Object.values(import.meta.glob<{ default: Run[] }>("../data/runs.json", { eager: true }))[0]?.default ?? [];

/** Runs in the 24 hours up to `until` that did not fail, with their mean and longest gap in minutes. */
export function runStats(until: string) {
  const end = Date.parse(until);
  const starts = runs
    .filter((r) => r.conclusion !== "failure" && r.conclusion !== "cancelled")
    .map((r) => Date.parse(r.createdAt))
    .filter((t) => t > end - 24 * 3600e3 && t <= end)
    .sort((a, b) => a - b);
  if (starts.length < 2) return null;
  const gaps = starts.slice(1).map((t, i) => (t - starts[i]) / 60e3);
  return {
    runs: starts.length,
    mean: Math.round(gaps.reduce((a, b) => a + b, 0) / gaps.length),
    longest: Math.round(Math.max(...gaps)),
  };
}
