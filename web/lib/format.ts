export const integer = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
export const decimal = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
export const percent = new Intl.NumberFormat("en-US", { style: "percent", maximumFractionDigits: 1 });

export function hours(value: number) {
  if (value < 24) return `${decimal.format(value)}h`;
  return `${decimal.format(value / 24)}d`;
}

export function shortDate(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

export function featureLabel(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}
