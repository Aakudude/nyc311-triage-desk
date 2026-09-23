"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { apiFetch } from "@/lib/api";
import { featureLabel, hours, percent, shortDate } from "@/lib/format";
import type { Meta, ScoreRequest, ScoreResponse, Ticket } from "@/lib/types";
import { useApi } from "@/hooks/use-api";
import { PageHeader } from "@/components/page-header";
import { ErrorState, LoadingState, Notice } from "@/components/states";

export function TicketView({ id }: { id: string }) {
  const [reveal, setReveal] = useState(false);
  const ticket = useApi<Ticket>(`/tickets/${encodeURIComponent(id)}?reveal_outcome=${reveal}`);
  const meta = useApi<Meta>("/meta");

  if (ticket.loading && !ticket.data) return <LoadingState waking={ticket.waking} />;
  if (ticket.error || !ticket.data) return <ErrorState message={ticket.error ?? "Ticket unavailable"} retry={ticket.reload} />;

  const data = ticket.data;
  return (
    <>
      <Link href="/queue" className="eyebrow">← Back to queue</Link>
      <PageHeader eyebrow={`${data.agency} · Ticket #${data.unique_key}`} title={data.complaint_type} description={data.descriptor ?? "No descriptor provided"} aside={<button className={`button ${reveal ? "" : "button-secondary"}`} onClick={() => setReveal((value) => !value)}>{reveal ? "Hide outcome" : "Reveal outcome"}</button>} />
      {reveal && <Notice tone="warn">The historical outcome is now visible. Keep it hidden when evaluating whether you would have escalated this ticket.</Notice>}

      <div className="detail-grid">
        <div>
          <section className="panel">
            <div className="panel-head"><div><h2>Creation-time risk</h2><p>Frozen when the ticket arrived</p></div><span className={`badge badge-${data.risk_band}`}>{data.risk_band} risk</span></div>
            <div className="panel-body">
              <div style={{ display: "grid", gridTemplateColumns: "150px 1fr", gap: 26, alignItems: "center" }}>
                <div style={{ width: 140, height: 140, borderRadius: "50%", display: "grid", placeItems: "center", background: `conic-gradient(${data.risk_band === "high" ? "#e26442" : "#1386a0"} ${data.risk * 360}deg, #e2e5e2 0)` }}><div style={{ width: 108, height: 108, borderRadius: "50%", display: "grid", placeItems: "center", background: "#fbfaf6", fontSize: 27, fontWeight: 800 }}>{percent.format(data.risk)}</div></div>
                <dl className="definition-list"><div><dt>Created</dt><dd>{shortDate(data.created_at)}</dd></div><div><dt>Portfolio threshold</dt><dd>{hours(data.sla_threshold_hrs)}</dd></div><div><dt>Borough</dt><dd>{data.borough}</dd></div><div><dt>Channel</dt><dd>{data.channel}</dd></div></dl>
              </div>
              {data.actual_outcome && <div className="score-result mt-20"><p>Revealed historical outcome</p><strong>{data.actual_outcome.breached ? "Breached" : "Within target"}</strong><p>{data.actual_outcome.resolution_hours == null ? "Still open at observation" : `Resolved in ${hours(data.actual_outcome.resolution_hours)}`}</p></div>}
            </div>
          </section>

          <section className="panel">
            <div className="panel-head"><div><h2>Why risk increased</h2><p>Positive SHAP contributions in model log-odds space</p></div></div>
            <div className="panel-body stack-list">{data.top_reasons.length ? data.top_reasons.map((reason) => <div className="reason-card" key={reason.feature}><strong>{reason.label}</strong><p>Value: {String(reason.value)}{reason.train_median != null ? ` · training median ${reason.train_median.toFixed(2)}` : ""}</p></div>) : <p className="muted">No positive contributors were exported for this ticket.</p>}</div>
          </section>
        </div>
        {meta.data ? <WhatIfPanel ticket={data} meta={meta.data} /> : <LoadingState waking={meta.waking} />}
      </div>
    </>
  );
}

function WhatIfPanel({ ticket, meta }: { ticket: Ticket; meta: Meta }) {
  const initialBacklog = Number(ticket.feature_values.agency_open_backlog ?? 0);
  const [agency, setAgency] = useState(ticket.agency);
  const [complaint, setComplaint] = useState(ticket.complaint_type);
  const [borough, setBorough] = useState(ticket.borough);
  const [channel, setChannel] = useState(ticket.channel);
  const [createdAt, setCreatedAt] = useState(ticket.created_at.slice(0, 16));
  const [backlog, setBacklog] = useState(String(initialBacklog));
  const [result, setResult] = useState<ScoreResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault(); setLoading(true); setError(null);
    const payload: ScoreRequest = { agency, complaint_type: complaint, borough, channel, created_at: createdAt, agency_open_backlog: Number(backlog) };
    try { setResult(await apiFetch<ScoreResponse>("/score", { method: "POST", body: JSON.stringify(payload) })); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Scoring failed"); }
    finally { setLoading(false); }
  }

  const complaints = meta.complaint_types_by_agency[agency] ?? [];
  return <aside><section className="panel"><div className="panel-head"><div><h2>What-if scorer</h2><p>Change conditions and rescore</p></div></div><div className="panel-body"><form className="form-grid" onSubmit={submit}>
    <div className="field"><label>Agency</label><select className="select" value={agency} onChange={(event) => { const next = event.target.value; setAgency(next); setComplaint(meta.complaint_types_by_agency[next]?.[0] ?? ""); }}>{meta.agencies.map((value) => <option key={value}>{value}</option>)}</select></div>
    <div className="field"><label>Borough</label><select className="select" value={borough} onChange={(event) => setBorough(event.target.value)}>{meta.boroughs.map((value) => <option key={value}>{value}</option>)}</select></div>
    <div className="field" style={{ gridColumn: "1 / -1" }}><label>Complaint type</label><select className="select" value={complaint} onChange={(event) => setComplaint(event.target.value)}>{complaints.map((value) => <option key={value}>{value}</option>)}</select></div>
    <div className="field"><label>Channel</label><select className="select" value={channel} onChange={(event) => setChannel(event.target.value)}>{meta.channels.map((value) => <option key={value}>{value}</option>)}</select></div>
    <div className="field"><label>Creation time</label><input className="input" type="datetime-local" value={createdAt} onChange={(event) => setCreatedAt(event.target.value)} /></div>
    <div className="field" style={{ gridColumn: "1 / -1" }}><label>Agency backlog</label><input className="input" type="number" min="0" value={backlog} onChange={(event) => setBacklog(event.target.value)} /></div>
    <button className="button" style={{ gridColumn: "1 / -1" }} disabled={loading}>{loading ? "Scoring…" : "Run scenario"}</button>
  </form>{error && <p className="notice notice-warn mt-20">{error}</p>}{result && <div className="score-result mt-20"><p>Scenario risk · {result.risk_band} band</p><strong>{percent.format(result.risk)}</strong><p>{result.defaulted_fields.length} remaining operational features used frozen defaults.</p></div>}</div></section>
  {result && <section className="panel"><div className="panel-head"><div><h2>Scenario reasons</h2><p>Risk-increasing factors</p></div></div><div className="panel-body stack-list">{result.reasons.map((reason) => <div className="reason-card" key={reason.feature}><strong>{reason.label}</strong><p>{featureLabel(reason.feature)} · {String(reason.value)}</p></div>)}</div></section>}</aside>;
}
