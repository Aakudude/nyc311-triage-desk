const API_URL = (
  process.env.NEXT_PUBLIC_API_URL ?? "https://nyc311-triage-api.onrender.com"
).replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    message: string,
    public status?: number,
  ) {
    super(message);
  }
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const retryable = new Set([502, 503, 504]);
  let lastError: unknown;

  for (let attempt = 0; attempt < 4; attempt += 1) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 60_000);
    try {
      const response = await fetch(`${API_URL}${path}`, {
        ...options,
        headers: {
          Accept: "application/json",
          ...(options.body ? { "Content-Type": "application/json" } : {}),
          ...options.headers,
        },
        signal: controller.signal,
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        const detail = typeof body?.detail === "string" ? body.detail : `Request failed (${response.status})`;
        if (retryable.has(response.status) && attempt < 3) {
          lastError = new ApiError(detail, response.status);
          await sleep(1_500 * (attempt + 1));
          continue;
        }
        throw new ApiError(detail, response.status);
      }
      return (await response.json()) as T;
    } catch (error) {
      if (error instanceof ApiError && !retryable.has(error.status ?? 0)) throw error;
      lastError = error;
      if (attempt < 3) {
        await sleep(1_500 * (attempt + 1));
        continue;
      }
    } finally {
      window.clearTimeout(timeout);
    }
  }

  const message = lastError instanceof Error ? lastError.message : "API request failed";
  throw new ApiError(message);
}

export function queryString(values: Record<string, string | number | boolean | undefined>) {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== "") params.set(key, String(value));
  });
  return params.toString();
}
