export function MetricCard({
  label,
  value,
  detail,
  accent = "blue",
}: {
  label: string;
  value: string;
  detail: string;
  accent?: "blue" | "orange" | "green" | "ink";
}) {
  return (
    <article className={`metric-card metric-${accent}`}>
      <span className="metric-label">{label}</span>
      <strong>{value}</strong>
      <p>{detail}</p>
    </article>
  );
}
