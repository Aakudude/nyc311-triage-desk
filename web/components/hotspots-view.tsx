"use client";

import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApi } from "@/hooks/use-api";
import { integer, percent } from "@/lib/format";
import type { BoroughHotspot, ComplaintHotspot, HotspotResponse } from "@/lib/types";
import { PageHeader } from "@/components/page-header";
import { ErrorState, LoadingState, Notice } from "@/components/states";

type Level = "borough" | "agency_complaint";

export function HotspotsView() {
  const [level, setLevel] = useState<Level>("borough");
  const response = useApi<HotspotResponse<BoroughHotspot | ComplaintHotspot>>(`/hotspots?level=${level}`);
  const sorted = useMemo(() => [...(response.data?.items ?? [])].sort((a, b) => b.breach_rate - a.breach_rate || b.n - a.n), [response.data]);
  const chartData = sorted.slice(0, 18).map((row) => ({ ...row, label: "borough" in row ? `${row.agency} · ${row.borough}` : `${row.agency} · ${row.complaint_type}` }));

  return (
    <>
      <PageHeader eyebrow="Capacity signals · all exported outcomes" title="Find where breaches concentrate." description="Compare borough and complaint-type cohorts to decide where operational attention may have the highest leverage." aside={<div className="segmented"><button className={level === "borough" ? "segment-active" : ""} onClick={() => setLevel("borough")}>Borough</button><button className={level === "agency_complaint" ? "segment-active" : ""} onClick={() => setLevel("agency_complaint")}>Complaint</button></div>} />
      {response.data && <Notice>{response.data.limitation}</Notice>}
      {response.loading && !response.data ? <LoadingState waking={response.waking} /> : response.error ? <ErrorState message={response.error} retry={response.reload} /> : response.data && (
        <div className="layout-grid">
          <section className="panel">
            <div className="panel-head"><div><h2>Highest observed breach rates</h2><p>Top {chartData.length} agency × {level === "borough" ? "borough" : "complaint"} cohorts</p></div></div>
            <div className="panel-body"><div className="chart-frame">
              <ResponsiveContainer width="100%" height="100%"><BarChart data={chartData} layout="vertical" margin={{ left: 14, right: 20 }}><CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#dfe2de" /><XAxis type="number" domain={[0, 0.6]} tickFormatter={(value) => percent.format(Number(value))} tickLine={false} axisLine={false} /><YAxis type="category" dataKey="label" width={150} tick={{ fontSize: 9 }} tickLine={false} axisLine={false} /><Tooltip formatter={(value) => percent.format(Number(value))} /><Bar dataKey="breach_rate" name="Breach rate" fill="#e26442" /></BarChart></ResponsiveContainer>
            </div></div>
          </section>
          <section className="panel">
            <div className="panel-head"><div><h2>Cohort detail</h2><p>Sorted by observed breach rate</p></div><span className="badge badge-blue">{response.data.items.length} cohorts</span></div>
            <div className="table-wrap"><table className="data-table"><thead><tr><th>Agency</th><th>{level === "borough" ? "Borough" : "Complaint"}</th><th>Tickets</th><th>Rate</th></tr></thead><tbody>{sorted.slice(0, 30).map((row) => <tr key={`${row.agency}-${"borough" in row ? row.borough : row.complaint_type}`}><td><span className="badge badge-blue">{row.agency}</span></td><td><strong>{"borough" in row ? row.borough : row.complaint_type}</strong></td><td>{integer.format(row.n)}</td><td>{percent.format(row.breach_rate)}</td></tr>)}</tbody></table></div>
          </section>
        </div>
      )}
    </>
  );
}
