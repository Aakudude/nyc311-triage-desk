"use client";

import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";

export function useApi<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(path));
  const [waking, setWaking] = useState(false);

  const load = useCallback(async () => {
    if (!path) return;
    setLoading(true);
    setError(null);
    const wakingTimer = window.setTimeout(() => setWaking(true), 2_500);
    try {
      setData(await apiFetch<T>(path));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to reach the API");
    } finally {
      window.clearTimeout(wakingTimer);
      setLoading(false);
      setWaking(false);
    }
  }, [path]);

  useEffect(() => {
    const loadTimer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(loadTimer);
  }, [load]);

  return { data, error, loading, waking, reload: load };
}
