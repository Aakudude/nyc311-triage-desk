"use client";

import Link from "next/link";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApi } from "@/hooks/use-api";
import { integer, percent } from "@/lib/format";
import type { Health, KpiResponse, ModelCard } from "@/lib/types";
import { MetricCard } from "@/components/metric-card";
import { PageHeader } from "@/components/page-header";
import { ErrorState, LoadingState, Notice } from "@/components/states";

export function OverviewDashboard() {
  const health = useApi<Health>("/health");
  const kpis = useApi<KpiResponse>("/kpis");
  const model = useApi<ModelCard>("/model-card");
  const loading = health.loading || kpis.loading || model.loading;
  const waking = health.waking || kpis.waking || model.waking;
  const error = health.error || kpis.error || model.error;

  if (loading && (!health.data || !kpis.data || !model.data)) return <LoadingState waking={waking} />;
  if (error || !health.data || !kpis.data || !model.data) {
    return <ErrorState message={error ?? "Incomplete API response"} retry={() => { void health.reload(); void kpis.reload(); void model.reload(); }} />;
  }

  const xgb = model.data.metrics.XGBoost;
  const chartData = [...kpis.data.items].sort((a, b) => b.n - a.n);
  const bestAgency = [...model.data.per_agency_pr_auc].sort((a, b) => b.pr_auc - a.pr_auc)[0];

  return (
    <>
      <PageHeader
        eyebrow="Daily command brief · historical replay"
        title="See the backlog before it becomes a breach."
        description="Risk-ranked 311 operations intelligence for supervisors deciding what to escalate now and where to add capacity next."
        aside={<Link href="/queue" className="button">Open triage queue →</Link>}
      />

      <div className="metric-grid">
        <MetricCard label="Replay population" value={integer.format(health.data.tickets_loaded)} detail="Tickets eligible across the frozen replay window" accent="ink" />
        <MetricCard label="Top-decile precision" value={percent.format(xgb.top_decile_precision)} detail={`Versus ${percent.format(xgb.base_rate)} without ranking`} accent="orange" />
        <MetricCard label="Top-decile lift" value={`${xgb.top_decile_lift.toFixed(2)}×`} detail="Breach concentration in the supervisor's first review band" accent="green" />
        <MetricCard label="Test PR-AUC" value={xgb.pr_auc.toFixed(3)} detail={`Lookup baseline: ${model.data.metrics.B1_group_lookup.pr_auc.toFixed(3)}`} accent="blue" />
      </div>

      <Notice>{kpis.data.limitation} These totals establish operating scale; replay decisions live in the queue.</Notice>

      <div className="layout-grid">
        <section className="panel">
          <div className="panel-head"><div><h2>Ticket volume by agency</h2><p>All exported records used for portfolio context</p></div><span className="badge badge-blue">6 agencies</span></div>
          <div className="panel-body">
            <div className="chart-frame">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartData} margin={{ top: 10, right: 8, left: 10, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#dfe2de" />
                  <XAxis dataKey="agency" tickLine={false} axisLine={false} />
                  <YAxis tickLine={false} axisLine={false} width={55} tickFormatter={(value) => `${Math.round(Number(value) / 1000)}k`} />
                  <Tooltip formatter={(value) => integer.format(Number(value))} cursor={{ fill: "#edf3f2" }} />
                  <Bar dataKey="n" name="Tickets" fill="#1386a0" radius={[2, 2, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </section>

        <aside>
          <section className="panel">
            <div className="panel-head"><div><h2>Supervisor readout</h2><p>What the model changes operationally</p></div></div>
            <div className="panel-body stack-list">
              <div className="list-row"><div><strong>Highest agency PR-AUC</strong><span>Best local ranking signal</span></div><b>{bestAgency.agency} · {bestAgency.pr_auc.toFixed(3)}</b></div>
              <div className="list-row"><div><strong>Breach capture</strong><span>Found in first 10% reviewed</span></div><b>{percent.format(xgb.top_decile_recall)}</b></div>
              <div className="list-row"><div><strong>Model calibration</strong><span>Validation ECE</span></div><b>{percent.format(model.data.calibration_method.validation_ece)}</b></div>
              <div className="list-row"><div><strong>Data accepted</strong><span>After hard quality rules</span></div><b>{percent.format(model.data.data_quality.silver_rows / model.data.data_quality.bronze_rows)}</b></div>
            </div>
          </section>
          <section className="panel">
            <div className="panel-body">
              <p className="eyebrow">Replay window</p>
              <strong>{new Date(health.data.replay_start).toLocaleDateString()} — {new Date(health.data.replay_end_exclusive).toLocaleDateString()}</strong>
              <p className="muted mt-20">This is a historical decision replay, not a live NYC operations feed.</p>
            </div>
          </section>
        </aside>
      </div>
    </>
  );
}
