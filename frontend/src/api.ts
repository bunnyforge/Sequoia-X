export async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok) {
    let detail = `请求失败：${response.status}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export type TrendPoint = { date: string; records: number };

export type DashboardSummary = {
  latest_trade_date: string | null;
  symbol_count: number;
  latest_symbol_count: number;
  tasks_today: number;
  synced_records: number;
  success_rate: number;
  pending_alerts: number;
  correctness_rate: number;
  completeness_rate: number;
  duplicate_rate: number;
  invalid_rows: number;
  stale_symbols: number;
  trend: TrendPoint[];
};

export type SyncJob = {
  id: number;
  name: string;
  source: string;
  processed: number;
  success_rate: number;
  status: string;
};

export type StaleAlert = {
  symbol: string;
  last_date: string;
  bars: number;
  reason: string;
};

export type InvalidAlert = {
  symbol: string;
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
  reason: string;
};

export type SyncFailure = {
  symbol: string;
  error?: string;
  reason?: string;
  at?: string;
};

export type AlertsPayload = {
  latest_trade_date: string | null;
  stale: StaleAlert[];
  invalid: InvalidAlert[];
  sync_failures: SyncFailure[];
  pending_count: number;
};

export type SyncJobStatus = {
  key: string;
  label: string;
  state: string;
  running: boolean;
  mode: string | null;
  attempt: number;
  current: number;
  total: number;
  symbol: string | null;
  progress: string | null;
  last_run_at: string | null;
  next_run_at: string | null;
  last_error: string | null;
  last_message: string | null;
};

export type SchedulerSnapshot = {
  config: {
    enabled: boolean;
    interval_seconds: number;
    retry_seconds: number;
    max_retries: number;
    concurrency: number;
    sleep_seconds: number;
    start_date: string;
  };
  state: string;
  running: boolean;
  last_run_at: string | null;
  next_run_at: string | null;
  last_message: string | null;
  last_error: string | null;
  attempt: number;
  progress: string | null;
  current: number;
  total: number;
  symbol: string | null;
  mode: string | null;
  jobs: SyncJobStatus[];
  recent_failures: SyncFailure[];
  recent_runs: Array<{
    at: string;
    ok: boolean;
    triggered: string;
    message: string;
    error: string | null;
    written: number;
    targets: number;
    failed_count: number;
  }>;
};

export function formatRate(value: number): string {
  return `${value.toFixed(2)}%`;
}

export function statusColor(status: string): string {
  if (status === "running") return "processing";
  if (status === "warning" || status === "error" || status === "retrying") return "warning";
  if (status === "idle") return "default";
  return "success";
}

export const STATUS_LABEL: Record<string, string> = {
  running: "运行中",
  completed: "已完成",
  warning: "部分停住",
  idle: "待执行",
  error: "失败",
  retrying: "重试中",
};

export type StrategyField = {
  key: string;
  label: string;
  type: "int" | "float" | "percent" | "text" | "date";
  min?: number;
  max?: number;
  default?: string | number;
};

export type StrategyItem = {
  key: string;
  name: string;
  kind: "scan" | "backtest";
  summary: string;
  fields: StrategyField[];
  enabled: boolean;
  params: Record<string, string | number>;
  running: boolean;
  last_run_at: string | null;
  ok: boolean;
  message: string | null;
  picks: string[];
  extra: Record<string, unknown>;
};

export type StrategiesPayload = {
  items: StrategyItem[];
};
