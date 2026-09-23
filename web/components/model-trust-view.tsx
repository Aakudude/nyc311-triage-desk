"use client";

import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApi } from "@/hooks/use-api";
import { featureLabel, integer, percent } from "@/lib/format";
import type { ModelCard } from "@/lib/types";
import { MetricCard } from "@/components/metric-card";
import { PageHeader } from "@/components/page-header";
import { ErrorState, LoadingState, Notice } from "@/components/states";

export function ModelTrustView() {
  const model = useApi<ModelCard>("/model-card");
  if (model.loading && !model.data) return <LoadingState waking={model.waking} />;
  if (model.error || !model.data) return <ErrorState message={model.error ?? "Model card unavailable"} retry={model.reload} />;

  const data = model.data;
  const xgb = data.metrics.XGBoost;
  const importance = data.global_shap.slice(0, 10).map((item) => ({ ...item, label: featureLabel(item.feature) }));
  const metricRows = Object.values(data.metrics);
  const accepted = data.data_quality.silver_rows / data.data_quality.bronze_rows;

  return (
    <>
      <PageHeader eyebrow="Evidence before automation · model v1" title="Trust is part of the product." description="The model earns its place only if it beats practical baselines, stays calibrated, and makes its data assumptions visible." aside={<span className="badge badge-normal">Validation ECE {percent.format(data.calibration_method.validation_ece)}</span>} />
      <div className="metric-grid">
        <MetricCard label="XGBoost PR-AUC" value={xgb.pr_auc.toFixed(3)} detail={`Base rate ${xgb.base_rate.toFixed(3)} · higher is better`} accent="blue" />
        <MetricCard label="ROC-AUC" value={xgb.roc_auc.toFixed(3)} detail="Modest discrimination; use for ranking, not automatic denial" accent="ink" />
        <MetricCard label="Top-decile lift" value={`${xgb.top_decile_lift.toFixed(2)}×`} detail={`${percent.format(xgb.top_decile_precision)} precision in first review band`} accent="orange" />
        <MetricCard label="Rows accepted" value={percent.format(accepted)} detail={`${integer.format(data.data_quality.rejected_rows)} hard-rule rejects quarantined`} accent="green" />
      </div>

      <Notice tone="warn"><strong>Portfolio assumption:</strong> {data.sla_definition}</Notice>

      <section className="panel mb-20">
        <div className="panel-head"><div><h2>Model versus operational baselines</h2><p>Untouched future test window</p></div></div>
        <div className="table-wrap"><table className="data-table"><thead><tr><th>Approach</th><th>PR-AUC</th><th>ROC-AUC</th><th>Brier ↓</th><th>Top 10% precision</th><th>Lift</th><th>Breach capture</th></tr></thead><tbody>{metricRows.map((row) => <tr key={row.model}><td><strong>{row.model.replaceAll("_", " ")}</strong></td><td>{row.pr_auc.toFixed(3)}</td><td>{row.roc_auc.toFixed(3)}</td><td>{row.brier.toFixed(3)}</td><td>{percent.format(row.top_decile_precision)}</td><td>{row.top_decile_lift.toFixed(2)}×</td><td>{percent.format(row.top_decile_recall)}</td></tr>)}</tbody></table></div>
      </section>

      <div className="layout-grid mb-20">
        <section className="panel">
          <div className="panel-head"><div><h2>Calibration</h2><p>Predicted risk against observed breach rate · hover for bin size</p></div></div>
          <div className="panel-body"><div className="chart-frame"><ResponsiveContainer width="100%" height="100%"><LineChart data={data.calibration}><CartesianGrid strokeDasharray="3 3" stroke="#dfe2de" /><XAxis dataKey="mean_pred" type="number" domain={[0, 0.8]} tickFormatter={(value) => percent.format(Number(value))} /><YAxis domain={[0, 1]} tickFormatter={(value) => percent.format(Number(value))} /><Tooltip formatter={(value, name) => [percent.format(Number(value)), name === "mean_pred" ? "Mean prediction" : "Observed rate"]} labelFormatter={(_, payload) => payload[0] ? `${payload[0].payload.n.toLocaleString()} tickets` : ""} /><Legend /><Line dataKey="mean_pred" name="Mean prediction" stroke="#1386a0" strokeWidth={2} /><Line dataKey="actual_rate" name="Observed rate" stroke="#e26442" strokeWidth={2} /></LineChart></ResponsiveContainer></div></div>
        </section>
        <section className="panel">
          <div className="panel-head"><div><h2>Global feature influence</h2><p>Mean absolute SHAP contribution</p></div></div>
          <div className="panel-body"><div className="chart-frame"><ResponsiveContainer width="100%" height="100%"><BarChart data={importance} layout="vertical" margin={{ left: 30 }}><CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#dfe2de" /><XAxis type="number" tickLine={false} axisLine={false} /><YAxis type="category" dataKey="label" width={150} tick={{ fontSize: 9 }} tickLine={false} axisLine={false} /><Tooltip /><Bar dataKey="mean_abs_shap" name="Mean |SHAP|" fill="#1386a0" /></BarChart></ResponsiveContainer></div></div>
        </section>
      </div>

      <div className="layout-grid">
        <section className="panel"><div className="panel-head"><div><h2>Data-quality reconciliation</h2><p>Hard failures are quarantined, not silently dropped</p></div></div><div className="panel-body definition-list"><div><dt>Bronze input</dt><dd>{integer.format(data.data_quality.bronze_rows)}</dd></div><div><dt>Silver accepted</dt><dd>{integer.format(data.data_quality.silver_rows)}</dd></div>{data.data_quality.rejects_by_reason.map((row) => <div key={row._reject_reason}><dt>{featureLabel(row._reject_reason)}</dt><dd>{integer.format(row.count)}</dd></div>)}</div></section>
        <section className="panel"><div className="panel-head"><div><h2>Known limitations</h2><p>What not to overclaim</p></div></div><div className="panel-body stack-list">{data.limitations.map((limitation, index) => <div className="list-row" key={limitation}><span className="mono">0{index + 1}</span><strong>{limitation}</strong></div>)}</div></section>
      </div>
    </>
  );
}
