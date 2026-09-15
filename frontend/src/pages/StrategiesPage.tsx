import { useEffect, useState } from "react";
import {
  Button,
  Card,
  Col,
  DatePicker,
  Form,
  Input,
  InputNumber,
  Row,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import dayjs from "dayjs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchJson, type StrategiesPayload, type StrategyItem, type StrategyField } from "../api";

function formatTime(value: string | null | undefined): string {
  if (!value) return "尚未运行";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function FieldControl({ field, ...rest }: { field: StrategyField } & Record<string, unknown>) {
  if (field.type === "text") {
    return <Input.TextArea autoSize={{ minRows: 2, maxRows: 4 }} {...rest} />;
  }
  if (field.type === "date") {
    return <DatePicker allowClear={false} style={{ width: "100%" }} {...rest} />;
  }
  const addon = field.type === "percent" ? "%" : undefined;
  const step = field.type === "int" ? 1 : field.type === "percent" ? 1 : 0.1;
  return (
    <InputNumber
      min={field.type === "percent" ? (field.min ?? 0) * 100 : field.min}
      max={field.type === "percent" ? (field.max ?? 1) * 100 : field.max}
      step={step}
      style={{ width: "100%" }}
      addonAfter={addon}
      {...rest}
    />
  );
}

function toFormValues(item: StrategyItem): Record<string, unknown> {
  const values: Record<string, unknown> = { enabled: item.enabled };
  for (const field of item.fields) {
    const raw = item.params[field.key] ?? field.default;
    if (field.type === "date") {
      values[field.key] = raw;
    } else if (field.type === "percent") {
      values[field.key] = Number(raw) * 100;
    } else {
      values[field.key] = raw;
    }
  }
  return values;
}

function fromFormValues(item: StrategyItem, values: Record<string, unknown>): { enabled: boolean; params: Record<string, unknown> } {
  const params: Record<string, unknown> = {};
  for (const field of item.fields) {
    const raw = values[field.key];
    if (field.type === "date") {
      params[field.key] = typeof raw === "string" ? raw : raw && typeof raw === "object" && "format" in raw
        ? (raw as { format: (value: string) => string }).format("YYYY-MM-DD")
        : field.default;
    } else if (field.type === "percent") {
      params[field.key] = Number(raw) / 100;
    } else if (field.type === "int") {
      params[field.key] = Number(raw);
    } else {
      params[field.key] = raw;
    }
  }
  return { enabled: Boolean(values.enabled), params };
}

function StrategyCard({ item }: { item: StrategyItem }) {
  const queryClient = useQueryClient();
  const [form] = Form.useForm();

  useEffect(() => {
    form.setFieldsValue(toFormValues(item));
  }, [form, item]);

  const saveMutation = useMutation({
    mutationFn: (payload: { enabled: boolean; params: Record<string, unknown> }) =>
      fetchJson<StrategiesPayload>(`/api/strategies/${item.key}`, {
        method: "PUT",
        body: JSON.stringify(payload),
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(["strategies"], data);
      message.success(`已保存「${item.name}」`);
    },
    onError: (error: Error) => message.error(error.message),
  });

  const runMutation = useMutation({
    mutationFn: () =>
      fetchJson<StrategiesPayload>(`/api/strategies/${item.key}/run`, { method: "POST" }),
    onSuccess: (data) => {
      queryClient.setQueryData(["strategies"], data);
      message.success(`已开始运行「${item.name}」`);
    },
    onError: (error: Error) => message.error(error.message),
  });

  const kindLabel = item.kind === "backtest" ? "回测" : "选股";
  const extra = item.extra?.results as Array<Record<string, unknown>> | undefined;

  return (
    <Card
      title={
        <Space>
          <span>{item.name}</span>
          <Tag color={item.kind === "backtest" ? "purple" : "blue"}>{kindLabel}</Tag>
          {item.running ? <Tag color="processing">运行中</Tag> : null}
        </Space>
      }
    >
      <Typography.Paragraph type="secondary">{item.summary}</Typography.Paragraph>
      <Form
        form={form}
        layout="vertical"
        initialValues={toFormValues(item)}
        onFinish={(values) => saveMutation.mutate(fromFormValues(item, values))}
      >
        <Form.Item name="enabled" label="启用" valuePropName="checked">
          <Switch checkedChildren="开" unCheckedChildren="关" />
        </Form.Item>
        <Row gutter={16}>
          {item.fields.map((field) => (
            <Col xs={24} md={field.type === "text" ? 24 : 12} key={field.key}>
              <Form.Item
                name={field.key}
                label={field.label}
                rules={[{ required: true, message: `请填写${field.label}` }]}
                getValueFromEvent={
                  field.type === "date"
                    ? (value) => (value ? value.format("YYYY-MM-DD") : "")
                    : undefined
                }
                getValueProps={
                  field.type === "date"
                    ? (value) => ({ value: value ? dayjs(value) : undefined })
                    : undefined
                }
              >
                <FieldControl field={field} />
              </Form.Item>
            </Col>
          ))}
        </Row>
        <Space wrap>
          <Button type="primary" htmlType="submit" loading={saveMutation.isPending}>
            保存配置
          </Button>
          <Button onClick={() => runMutation.mutate()} loading={item.running || runMutation.isPending}>
            {item.kind === "backtest" ? "运行回测" : "运行选股"}
          </Button>
        </Space>
      </Form>
      <div className="section">
        <Typography.Text type="secondary">
          上次运行：{formatTime(item.last_run_at)}
          {item.message ? `　${item.message}` : ""}
        </Typography.Text>
        {item.kind === "backtest" && extra?.length ? (
          <Table
            className="section"
            rowKey="symbol"
            pagination={false}
            size="small"
            dataSource={extra}
            columns={[
              { title: "代码", dataIndex: "symbol" },
              { title: "收益%", dataIndex: "return_pct" },
              { title: "最大回撤%", dataIndex: "max_drawdown_pct" },
              { title: "轮次", dataIndex: "cycles" },
              { title: "最深层数", dataIndex: "max_layer_used" },
              { title: "期末资金", dataIndex: "equity" },
            ]}
          />
        ) : null}
        {item.kind === "scan" ? (
          <Typography.Paragraph style={{ marginTop: 12 }}>
            {item.picks.length ? `选出：${item.picks.join("、")}` : "暂无选股结果"}
          </Typography.Paragraph>
        ) : null}
      </div>
    </Card>
  );
}

export default function StrategiesPage() {
  const [active, setActive] = useState<string>("double_buy");
  const query = useQuery({
    queryKey: ["strategies"],
    queryFn: () => fetchJson<StrategiesPayload>("/api/strategies"),
    refetchInterval: 3000,
  });
  const items = query.data?.items ?? [];
  const current = items.find((item) => item.key === active) ?? items[0];

  return (
    <>
      <Card>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 16 }}>
          每个策略独立开关和参数，保存在数据库里。选股策略扫本地日 K；二倍买入法按你填的股票列表回测。
        </Typography.Paragraph>
        <Space wrap>
          {items.map((item) => (
            <Button
              key={item.key}
              type={current?.key === item.key ? "primary" : "default"}
              onClick={() => setActive(item.key)}
            >
              {item.name}
              {item.enabled ? " · 开" : ""}
            </Button>
          ))}
        </Space>
      </Card>
      <div className="section">
        {current ? <StrategyCard key={current.key} item={current} /> : <Card loading={query.isLoading} />}
      </div>
    </>
  );
}
