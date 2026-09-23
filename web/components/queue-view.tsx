"use client";

import Link from "next/link";
import { FormEvent, useMemo, useState } from "react";
import { useApi } from "@/hooks/use-api";
import { queryString } from "@/lib/api";
import { hours, integer, percent, shortDate } from "@/lib/format";
import type { Meta, QueueResponse } from "@/lib/types";
import { PageHeader } from "@/components/page-header";
import { ErrorState, LoadingState, Notice } from "@/components/states";

type Filters = { asOf: string; agency: string; borough: string; minRisk: string; reveal: boolean };
const INITIAL: Filters = { asOf: "2025-10-25T12:00", agency: "", borough: "", minRisk: "0", reveal: false };

export function QueueView() {
  const meta = useApi<Meta>("/meta");
  const [draft, setDraft] = useState(INITIAL);
  const [applied, setApplied] = useState(INITIAL);
  const queuePath = useMemo(() => `/queue?${queryString({
    as_of: applied.asOf,
    agency: applied.agency || undefined,
    borough: applied.borough || undefined,
    min_risk: applied.minRisk,
    reveal_outcome: applied.reveal,
    limit: 100,
  })}`, [applied]);
  const queue = useApi<QueueResponse>(queuePath);

  function submit(event: FormEvent) {
    event.preventDefault();
    setApplied({ ...draft });
  }

  return (
    <>
      <PageHeader eyebrow="Decision queue · creation-time risk" title="Work the tickets most likely to stall." description="Choose a historical moment. The queue reconstructs what was open then and ranks tickets using only information available when each ticket arrived." aside={queue.data && <div className="text-right"><span className="eyebrow">Open after filters</span><strong>{integer.format(queue.data.total)}</strong></div>} />

      <form className="controls" onSubmit={submit}>
        <div className="field field-grow"><label htmlFor="as-of">Replay time</label><input id="as-of" className="input" type="datetime-local" value={draft.asOf} min="2025-10-22T00:00" max="2025-10-31T23:59" onChange={(event) => setDraft({ ...draft, asOf: event.target.value })} required /></div>
        <div className="field"><label htmlFor="agency">Agency</label><select id="agency" className="select" value={draft.agency} onChange={(event) => setDraft({ ...draft, agency: event.target.value })}><option value="">All agencies</option>{meta.data?.agencies.map((agency) => <option key={agency}>{agency}</option>)}</select></div>
        <div className="field"><label htmlFor="borough">Borough</label><select id="borough" className="select" value={draft.borough} onChange={(event) => setDraft({ ...draft, borough: event.target.value })}><option value="">All boroughs</option>{meta.data?.boroughs.map((borough) => <option key={borough}>{borough}</option>)}</select></div>
        <div className="field"><label htmlFor="risk">Minimum risk</label><select id="risk" className="select" value={draft.minRisk} onChange={(event) => setDraft({ ...draft, minRisk: event.target.value })}><option value="0">Any risk</option><option value="0.25">25%+</option><option value="0.3139126">High band</option><option value="0.5">50%+</option></select></div>
        <label className="check-field"><input type="checkbox" checked={draft.reveal} onChange={(event) => setDraft({ ...draft, reveal: event.target.checked })} /> Reveal outcomes</label>
        <button className="button" type="submit">Apply filters</button>
      </form>

      <Notice tone={draft.reveal ? "warn" : "info"}>{draft.reveal ? "Historical outcomes are visible. Use this only after making a replay decision." : "Outcomes are hidden. “Already breached” means current replay age is beyond the portfolio threshold—not that the eventual outcome has been revealed."}</Notice>

      {queue.loading && !queue.data ? <LoadingState waking={queue.waking} /> : queue.error ? <ErrorState message={queue.error} retry={queue.reload} /> : queue.data && (
        <section className="panel">
          <div className="panel-head"><div><h2>Risk-ranked open tickets</h2><p>As of {shortDate(queue.data.as_of)} · showing {queue.data.count} of {integer.format(queue.data.total)}</p></div><span className="badge badge-blue">Risk ↓ · ID ↑</span></div>
          <div className="table-wrap">
            <table className="data-table">
              <thead><tr><th>Ticket</th><th>Agency</th><th>Complaint</th><th>Borough</th><th>Age</th><th>Risk</th><th>State</th>{applied.reveal && <th>Outcome</th>}</tr></thead>
              <tbody>{queue.data.items.map((item) => (
                <tr key={item.unique_key}>
                  <td><Link className="table-link mono" href={`/ticket?id=${encodeURIComponent(item.unique_key)}`}>#{item.unique_key}</Link><div className="muted">{shortDate(item.created_at)}</div></td>
                  <td><span className="badge badge-blue">{item.agency}</span></td>
                  <td><strong>{item.complaint_type}</strong></td>
                  <td>{item.borough}</td>
                  <td>{hours(item.age_hours)}</td>
                  <td className="risk-cell"><div className="risk-row"><div className="risk-track"><div className={`risk-fill ${item.risk_band === "high" ? "risk-fill-high" : ""}`} style={{ width: `${item.risk * 100}%` }} /></div><strong>{percent.format(item.risk)}</strong></div></td>
                  <td>{item.already_breached ? <span className="badge badge-breached">Already breached</span> : <span className={`badge badge-${item.risk_band}`}>{item.risk_band} risk</span>}</td>
                  {applied.reveal && <td>{item.actual_outcome?.breached ? <span className="badge badge-breached">Breached</span> : <span className="badge badge-normal">Within target</span>}</td>}
                </tr>
              ))}</tbody>
            </table>
            {queue.data.items.length === 0 && <div className="empty">No tickets match these filters.</div>}
          </div>
        </section>
      )}
    </>
  );
}
