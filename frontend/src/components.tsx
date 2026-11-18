import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Empty, Space, Spin, Tag, Typography } from "antd";
import * as echarts from "echarts/core";
import { BarChart, LineChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  AriaComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { api, ApiError } from "./api";
echarts.use([
  BarChart,
  LineChart,
  GridComponent,
  TooltipComponent,
  AriaComponent,
  CanvasRenderer,
]);
export function useData<T>(path: string | null, refresh = 0) {
  const [state, set] = useState<{ data?: T; error?: Error; loading: boolean }>({
    loading: !!path,
  });
  useEffect(() => {
    if (!path) {
      set({ loading: false });
      return;
    }
    const controller = new AbortController();
    set({ loading: true });
    api<T>(path, { signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted) set({ data, loading: false });
      })
      .catch((error) => {
        if (!controller.signal.aborted) set({ error, loading: false });
      });
    return () => controller.abort();
  }, [path, refresh]);
  return state;
}
export function ErrorBox({
  error,
  retry,
}: {
  error: Error;
  retry?: () => void;
}) {
  const title =
    error instanceof ApiError
      ? (
          {
            401: "会话已失效，请重新登录",
            404: "资源不存在或无项目权限",
            409: "数据尚不可用或版本已更新",
            410: "原始产物已不可用",
            503: "服务暂不可用",
          } as Record<number, string>
        )[error.status] || "请求未完成"
      : "请求未完成";
  return (
    <Alert
      type="error"
      showIcon
      title={title}
      description={
        <>
          <p>{error.message}</p>
          <a href="/login">重新登录</a>
          {retry && <Button onClick={retry}>重试</Button>}
        </>
      }
    />
  );
}
export function Loading() {
  return (
    <div className="loading">
      <Spin tip="读取真实分析数据" size="large">
        <div style={{ height: 80 }} />
      </Spin>
    </div>
  );
}
export function Blank({ text }: { text: string }) {
  return <Empty description={text} />;
}
export function Bytes({
  value,
  delta = false,
}: {
  value: number | null | undefined;
  delta?: boolean;
}) {
  if (value === null || value === undefined)
    return <span title="未采集或无基线，不能视为零">—</span>;
  const label = `${delta && value > 0 ? "+" : ""}${(value / 1048576).toFixed(2)} MiB`;
  return (
    <Typography.Text
      className="number"
      copyable={{ text: String(value) }}
      title={`${value} bytes`}
    >
      {label}
    </Typography.Text>
  );
}
export const stateText: Record<string, string> = {
  UPLOADING: "上传中",
  SEALED: "已封存",
  EXPIRED: "上传过期",
  PENDING: "未排队",
  QUEUED: "排队中",
  RUNNING: "分析中",
  READY: "分析完成",
  PARTIAL: "部分分析",
  FAILED: "分析失败",
  COMPARED: "已比较",
  NO_BASELINE: "首版无基线",
  complete: "完整",
  missing: "缺失",
  failed: "失败",
  unsupported: "不支持",
  not_requested: "未采集",
  partial: "部分",
  unavailable: "不可用",
  observed: "已观测事实",
  correlated: "有证据关联",
  hypothesis: "待验证假设",
  unreviewed: "未复核",
  confirmed: "已确认",
  rejected: "已拒绝",
  PASS: "未超阈值",
  WARN: "建议检查",
  SKIPPED: "门禁跳过",
  mismatched_range: "Git 范围不匹配",
};
export function Status({ value }: { value: string }) {
  return (
    <Tag
      color={
        ["FAILED", "failed", "WARN"].includes(value)
          ? "volcano"
          : ["READY", "complete", "PASS", "COMPARED"].includes(value)
            ? "cyan"
            : "default"
      }
    >
      {stateText[value] || value}
    </Tag>
  );
}
export function Pager({
  offset,
  next,
  onChange,
}: {
  offset: number;
  next: number | null | undefined;
  onChange: (n: number) => void;
}) {
  return (
    <Space className="pager">
      <Button
        disabled={!offset}
        onClick={() => onChange(Math.max(0, offset - 25))}
      >
        上页
      </Button>
      <span>第 {Math.floor(offset / 25) + 1} 页</span>
      <Button
        disabled={next == null}
        onClick={() => next != null && onChange(next)}
      >
        下页
      </Button>
    </Space>
  );
}
export function Json({ value }: { value: unknown }) {
  return <pre>{JSON.stringify(value, null, 2)}</pre>;
}
export function Chart({
  labels,
  values,
  kind = "bar",
  label,
}: {
  labels: string[];
  values: (number | null)[];
  kind?: "bar" | "line";
  label: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chart.setOption({
      animation: false,
      aria: { enabled: true },
      color: ["#157d7b"],
      grid: { left: 65, right: 30, top: 25, bottom: 55 },
      tooltip: { trigger: "axis", renderMode: "richText" },
      xAxis: {
        type: "category",
        data: labels,
        axisLabel: { rotate: labels.length > 8 ? 25 : 0 },
      },
      yAxis: { type: "value", name: "MiB" },
      series: [
        {
          name: label,
          type: kind,
          data: values.map((v) => (v === null ? null : v / 1048576)),
          connectNulls: false,
          symbolSize: 7,
          barMaxWidth: 42,
        },
      ],
    });
    const resize = new ResizeObserver(() => chart.resize());
    resize.observe(ref.current);
    return () => {
      resize.disconnect();
      chart.dispose();
    };
  }, [labels, values, kind, label]);
  return (
    <Card title={label}>
      <div
        role="img"
        aria-label={label}
        ref={ref}
        style={{ height: 280, width: "100%" }}
      />
      <details>
        <summary>查看图表原始 bytes</summary>
        <Json value={labels.map((label, i) => ({ label, bytes: values[i] }))} />
      </details>
    </Card>
  );
}
