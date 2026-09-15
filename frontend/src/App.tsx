import { useState } from "react";
import { Layout, Menu, Tag, Typography } from "antd";
import { FundOutlined, SyncOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import SyncPage from "./pages/SyncPage";
import StrategiesPage from "./pages/StrategiesPage";
import { fetchJson, type SchedulerSnapshot } from "./api";
import "./styles.css";

const { Header, Sider, Content } = Layout;

const TITLES: Record<string, { title: string; subtitle: string }> = {
  sync: { title: "同步任务", subtitle: "先更新股票池，再增量；全量请改起始日后手动执行" },
  strategies: { title: "策略中心", subtitle: "配置多个策略，分别开关、改参数、运行选股或回测" },
};

export default function App() {
  const [page, setPage] = useState("sync");
  const schedulerQuery = useQuery({
    queryKey: ["scheduler"],
    queryFn: () => fetchJson<SchedulerSnapshot>("/api/scheduler"),
    refetchInterval: 3000,
  });
  const runningCount = (schedulerQuery.data?.jobs ?? []).filter((job) => job.running).length;
  const running = runningCount > 0 || Boolean(schedulerQuery.data?.running);
  const healthy = schedulerQuery.data?.state !== "error";
  const meta = TITLES[page];

  return (
    <Layout className="app">
      <Sider theme="light" width={236}>
        <div className="brand">
          <span className="brand-mark">SX</span>
          <div>
            <b>Sequoia-X</b>
            <small>数据同步控制台</small>
          </div>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[page]}
          onClick={(event) => setPage(event.key)}
          items={[
            { key: "sync", icon: <SyncOutlined />, label: "同步任务" },
            { key: "strategies", icon: <FundOutlined />, label: "策略中心" },
          ]}
        />
      </Sider>
      <Layout>
        <Header className="topbar">
          <div>
            <Typography.Title level={3}>{meta.title}</Typography.Title>
            <span className="muted">{meta.subtitle}</span>
          </div>
          <Tag color={running ? "processing" : healthy ? "success" : "warning"}>
            {running ? `正在同步（${runningCount || 1}）` : healthy ? "系统运行正常" : "同步异常"}
          </Tag>
        </Header>
        <Content className="content">
          {page === "sync" ? <SyncPage /> : <StrategiesPage />}
        </Content>
      </Layout>
    </Layout>
  );
}
