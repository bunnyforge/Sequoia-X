import { useEffect } from "react";
import { Button, Card, DatePicker, Form, InputNumber, Modal, Progress, Space, Switch, Table, Tag, Typography, message } from "antd";
import dayjs from "dayjs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchJson, statusColor, STATUS_LABEL, type SchedulerSnapshot, type SyncJobStatus } from "../api";

function formatTime(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function formatTrigger(value: string): string {
  if (value.includes("full")) return "全量";
  if (value.includes("schedule")) return "定时增量";
  return "手动增量";
}

function jobPercent(job: SyncJobStatus): number {
  if (job.total) return Math.round((job.current / job.total) * 100);
  if (job.running) return 0;
  if (job.current) return 100;
  return 0;
}

export default function SyncPage() {
  const queryClient = useQueryClient();
  const [form] = Form.useForm();
  const schedulerQuery = useQuery({
    queryKey: ["scheduler"],
    queryFn: () => fetchJson<SchedulerSnapshot>("/api/scheduler"),
    refetchInterval: 2000,
  });
  const snapshot = schedulerQuery.data;
  const jobs = snapshot?.jobs ?? [];
  const incrementalBusy = Boolean(jobs.find((job) => job.key === "incremental")?.running);
  const fullBusy = Boolean(jobs.find((job) => job.key === "full")?.running);

  useEffect(() => {
    if (snapshot?.config) {
      form.setFieldsValue(snapshot.config);
    }
  }, [form, snapshot?.config]);

  const saveMutation = useMutation({
    mutationFn: (values: SchedulerSnapshot["config"]) =>
      fetchJson<SchedulerSnapshot>("/api/scheduler", {
        method: "PUT",
        body: JSON.stringify(values),
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(["scheduler"], data);
      message.success("已保存同步配置");
    },
    onError: (error: Error) => message.error(error.message),
  });

  const runMutation = useMutation({
    mutationFn: (mode: "incremental" | "full") =>
      fetchJson<SchedulerSnapshot>("/api/scheduler/run", {
        method: "POST",
        body: JSON.stringify({ mode }),
      }),
    onSuccess: (data, mode) => {
      queryClient.setQueryData(["scheduler"], data);
      message.success(mode === "full" ? "已开始全量同步" : "已开始增量同步");
    },
    onError: (error: Error) => message.error(error.message),
  });

  const runFull = () => {
    Modal.confirm({
      title: "执行全量同步？",
      content: "会先更新股票池（新增从起始日补、退市删除），再从起始日补缺失日 K，已有数据会跳过。可能较慢。可与定时/立即增量同时执行，进度会分行显示。",
      okText: "开始全量",
      cancelText: "取消",
      onOk: () => runMutation.mutateAsync("full"),
    });
  };

  return (
    <>
      <Card title="同步配置">
        <Typography.Paragraph type="secondary">
          定时增量、立即增量和全量分行显示，也可以同时跑。
          某一只股票某一天失败后，只停这一只后面的日期，其它股票继续同步。
          整轮任务只有登录失败、拉不到股票列表这类问题才会整轮重试。
        </Typography.Paragraph>
        <Form
          form={form}
          layout="vertical"
          initialValues={{
            enabled: false,
            interval_seconds: 300,
            retry_seconds: 30,
            max_retries: 3,
            start_date: "2024-01-01",
          }}
          onFinish={(values) => saveMutation.mutate(values)}
        >
          <Form.Item name="enabled" label="启用定时增量" valuePropName="checked">
            <Switch checkedChildren="开" unCheckedChildren="关" />
          </Form.Item>
          <Form.Item
            name="start_date"
            label="起始日期"
            rules={[{ required: true, message: "请选择起始日期" }]}
            getValueFromEvent={(value) => (value ? value.format("YYYY-MM-DD") : "")}
            getValueProps={(value) => ({ value: value ? dayjs(value) : undefined })}
          >
            <DatePicker allowClear={false} style={{ width: 240 }} />
          </Form.Item>
          <Form.Item
            name="interval_seconds"
            label="增量间隔（秒）"
            rules={[{ required: true, message: "请填写同步间隔" }]}
          >
            <InputNumber min={10} max={86400} style={{ width: 240 }} addonAfter="秒" />
          </Form.Item>
          <Form.Item
            name="retry_seconds"
            label="失败后重试间隔（秒）"
            rules={[{ required: true, message: "请填写重试间隔" }]}
          >
            <InputNumber min={5} max={3600} style={{ width: 240 }} addonAfter="秒" />
          </Form.Item>
          <Form.Item
            name="max_retries"
            label="单轮最多重试次数"
            rules={[{ required: true, message: "请填写重试次数" }]}
          >
            <InputNumber min={0} max={20} style={{ width: 240 }} />
          </Form.Item>
          <Space wrap>
            <Button type="primary" htmlType="submit" loading={saveMutation.isPending}>
              保存配置
            </Button>
            <Button onClick={() => runMutation.mutate("incremental")} loading={incrementalBusy || (runMutation.isPending && runMutation.variables === "incremental")}>
              立即增量
            </Button>
            <Button danger onClick={runFull} disabled={fullBusy || (runMutation.isPending && runMutation.variables === "full")}>
              全量同步
            </Button>
          </Space>
        </Form>
      </Card>
      <Card title="运行状态" className="section">
        <Table
          rowKey="key"
          pagination={false}
          dataSource={jobs}
          columns={[
            { title: "类型", dataIndex: "label", width: 110 },
            {
              title: "状态",
              width: 110,
              render: (_: unknown, job: SyncJobStatus) => {
                const state = job.running ? (job.state === "retrying" ? "retrying" : "running") : job.state;
                return <Tag color={statusColor(state)}>{STATUS_LABEL[state] ?? state}</Tag>;
              },
            },
            {
              title: "进度",
              width: 280,
              render: (_: unknown, job: SyncJobStatus) => (
                <div>
                  <Progress
                    percent={jobPercent(job)}
                    size="small"
                    status={
                      job.running && job.state !== "retrying"
                        ? "active"
                        : job.state === "error"
                          ? "exception"
                          : job.state === "warning"
                            ? "normal"
                            : "normal"
                    }
                  />
                  <div>
                    {job.total ? (
                      <Typography.Text>
                        已处理 {job.current} / {job.total}（{jobPercent(job)}%）
                        {job.symbol ? `，当前 ${job.symbol}` : ""}
                      </Typography.Text>
                    ) : (
                      <Typography.Text type="secondary">{job.last_message || job.progress || "尚未开始"}</Typography.Text>
                    )}
                  </div>
                </div>
              ),
            },
            {
              title: "失败原因",
              dataIndex: "last_error",
              render: (value: string | null) =>
                value ? (
                  <Typography.Text type="danger" style={{ whiteSpace: "normal" }}>
                    {value}
                  </Typography.Text>
                ) : (
                  "—"
                ),
            },
            {
              title: "下次执行",
              dataIndex: "next_run_at",
              width: 170,
              render: (value: string | null, job: SyncJobStatus) => (job.key === "schedule" ? formatTime(value) : "—"),
            },
            { title: "上次完成", dataIndex: "last_run_at", width: 170, render: formatTime },
          ]}
        />
      </Card>
      {(snapshot?.recent_failures?.length ?? 0) > 0 ? (
        <Card title="已停住的股票（只停这一只，其它股票继续同步）" className="section">
          <Table
            rowKey="symbol"
            pagination={{ pageSize: 8 }}
            dataSource={snapshot?.recent_failures ?? []}
            columns={[
              { title: "代码", dataIndex: "symbol", width: 120 },
              { title: "停在日期", dataIndex: "at", width: 140 },
              { title: "原因", dataIndex: "reason", render: (_: string, row) => row.reason || row.error || "—" },
            ]}
          />
        </Card>
      ) : null}
      <Card title="最近运行记录" className="section">
        <Table
          rowKey="at"
          pagination={false}
          dataSource={snapshot?.recent_runs ?? []}
          columns={[
            { title: "时间", dataIndex: "at", render: formatTime },
            { title: "触发", dataIndex: "triggered", render: formatTrigger },
            {
              title: "结果",
              dataIndex: "ok",
              render: (ok: boolean) => <Tag color={ok ? "success" : "error"}>{ok ? "成功" : "失败"}</Tag>,
            },
            { title: "目标股票", dataIndex: "targets" },
            { title: "写入条数", dataIndex: "written" },
            { title: "失败数", dataIndex: "failed_count" },
            {
              title: "说明",
              render: (_: unknown, row: { message: string; error: string | null }) => (
                <span>
                  {row.message}
                  {row.error ? (
                    <Typography.Text type="danger">　{row.error}</Typography.Text>
                  ) : null}
                </span>
              ),
            },
          ]}
        />
      </Card>
    </>
  );
}
