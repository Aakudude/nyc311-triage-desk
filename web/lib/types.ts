export type Health = {
  status: string;
  model_version: string;
  replay_start: string;
  replay_end_exclusive: string;
  snapshot_meaning: string;
  tickets_loaded: number;
};

export type SlaThreshold = {
  agency: string;
  complaint_type: string;
  n_train_closed: number;
  sla_target_hours: number;
  agency_p75: number;
  target_source: string;
};

export type Meta = {
  model_version: string;
  timezone: string;
  agencies: string[];
  boroughs: string[];
  channels: string[];
  complaint_types_by_agency: Record<string, string[]>;
  valid_replay_range: { start: string; end_exclusive: string };
  sla_thresholds: SlaThreshold[];
  portfolio_sla_assumption: string;
};

export type KpiItem = { agency: string; n: number; breach_rate: number };
export type KpiResponse = {
  grain: string;
  requested_period: { from: string | null; to: string | null };
  temporal_filter_applied: boolean;
  limitation: string;
  items: KpiItem[];
};

export type ActualOutcome = {
  breached: boolean;
  label_state?: string;
  closed_at: string | null;
  resolution_hours: number | null;
};

export type QueueItem = {
  unique_key: string;
  agency: string;
  complaint_type: string;
  borough: string;
  created_at: string;
  age_hours: number;
  risk: number;
  risk_band: string;
  sla_threshold_hrs: number;
  already_breached: boolean;
  actual_outcome?: ActualOutcome;
};

export type QueueResponse = {
  as_of: string;
  timezone: string;
  total: number;
  count: number;
  items: QueueItem[];
};

export type Reason = {
  feature: string;
  label: string;
  value: unknown;
  train_median: number | null;
  direction: string;
  contribution_log_odds: number;
};

export type Ticket = {
  unique_key: string;
  agency: string;
  complaint_type: string;
  descriptor: string | null;
  borough: string;
  channel: string;
  status_at_extract: string;
  created_at: string;
  risk: number;
  risk_band: string;
  sla_threshold_hrs: number;
  top_reasons: Reason[];
  feature_values: Record<string, string | number | null>;
  actual_outcome?: ActualOutcome;
};

export type ScoreRequest = {
  agency: string;
  complaint_type: string;
  borough: string;
  channel: string;
  created_at: string;
  agency_open_backlog?: number;
  complaint_open_backlog?: number;
  agency_arrivals_24h?: number;
  agency_closures_24h?: number;
  hist_median_res_hrs_28d?: number;
  hist_breach_rate_28d?: number;
};

export type ScoreResponse = {
  model_version: string;
  agency: string;
  complaint_type: string;
  complaint_type_grouped: string;
  risk: number;
  risk_band: "high" | "normal";
  sla_threshold_hrs: number;
  sla_source: string;
  reasons: Reason[];
  defaulted_fields: Array<{ feature: string; source: string }>;
  defaults_used: boolean;
};

export type ComplaintHotspot = {
  agency: string;
  complaint_type: string;
  n: number;
  breach_rate: number;
};
export type BoroughHotspot = {
  agency: string;
  borough: string;
  n: number;
  breach_rate: number;
};
export type HotspotResponse<T> = {
  level: string;
  requested_period: string | null;
  temporal_filter_applied: boolean;
  limitation: string;
  items: T[];
};

export type MetricResult = {
  model: string;
  pr_auc: number;
  roc_auc: number;
  brier: number;
  base_rate: number;
  top_decile_precision: number;
  top_decile_lift: number;
  top_decile_recall: number;
  n_top: number;
  tie_policy: string;
};

export type ModelCard = {
  model_version: string;
  sla_definition: string;
  metrics: Record<string, MetricResult>;
  per_agency_pr_auc: Array<{ agency: string; n: number; pr_auc: number; base_rate: number }>;
  calibration_method: { method: string; validation_ece: number };
  calibration: Array<{ bin: number; n: number; mean_pred: number; actual_rate: number }>;
  global_shap: Array<{ feature: string; mean_abs_shap: number }>;
  data_quality: {
    bronze_rows: number;
    silver_rows: number;
    rejected_rows: number;
    rejects_by_reason: Array<{ _reject_reason: string; count: number }>;
    label_states: Record<string, number>;
  };
  limitations: string[];
};
