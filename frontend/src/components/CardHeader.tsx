import type { Render } from "../api/types.gen";

function clockOf(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function ageMinutes(iso: string | null | undefined): number {
  if (!iso) return Infinity;
  return (Date.now() - new Date(iso).getTime()) / 60000;
}

/** The trust surface. The manager cannot read SQL by construction, so this
 *  block -- not the chart -- is what they judge the number by. Every value
 *  here is generated deterministically from the compiled query. */
export default function CardHeader({
  render,
  ttlSeconds,
}: {
  render: Render;
  ttlSeconds: number;
}) {
  if (!render.restatement) return null;
  const stale = ageMinutes(render.fetched_at) > ttlSeconds / 60;

  return (
    <>
      <p className="restatement">{render.restatement}</p>
      <div className="facts">
        <span className="fact">
          <b>{(render.row_count ?? 0).toLocaleString()}</b>{" "}
          {render.row_count === 1 ? "row" : "rows"}
        </span>
        {render.data_max_ts && (
          <span className="fact">
            data through <b>{render.data_max_ts.slice(0, 10)}</b>
          </span>
        )}
        <span className={stale ? "fact stale" : "fact"}>
          as of <b>{clockOf(render.fetched_at)}</b>
          {render.from_cache ? " · cached" : ""}
        </span>
      </div>
    </>
  );
}
