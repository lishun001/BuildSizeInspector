import { useEffect, useState } from "react";
import {
  Link,
  Navigate,
  Route,
  Routes,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Drawer,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Table,
  Tabs,
  Typography,
} from "antd";
import { api, setCsrf } from "./api";
import {
  Blank,
  Bytes,
  Chart,
  ErrorBox,
  Json,
  Loading,
  Pager,
  Status,
  useData,
} from "./components";
import type {
  Build,
  Change,
  Comparison,
  Entry,
  EvidenceDetail,
  FindingItem,
  History,
  Page,
  Project,
  Rules,
  UnityAsset,
  UnityChange,
} from "./types";

type ConfigChange = NonNullable<Comparison["result"]>["config_changes"][number];
function Login({ done }: { done: (name: string) => void }) {
  const [error, setError] = useState<Error>();
  const [busy, setBusy] = useState(false);
  return (
    <main className="login">
      <section className="login-intro">
        <span className="eyebrow">UNITY · BUILD INTELLIGENCE</span>
        <h1>
          每一次增长，
          <br />
          都有据可查。
        </h1>
        <p>
          从 APK 物理字节到 Unity 资源，
          <br />
          看清版本之间真正发生的变化。
        </p>
        <div className="formula">APK = ZIP 压缩条目 + 容器开销</div>
      </section>
      <Card className="login-card">
        <div className="brand">
          ▦ BuildSize <small>INSPECTOR</small>
        </div>
        <h2>登录工作台</h2>
        <p className="muted">使用管理员分配的账号访问已授权项目。</p>
        {error && <ErrorBox error={error} />}
        <Form
          layout="vertical"
          onFinish={async (values: { username: string; password: string }) => {
            setBusy(true);
            setError(undefined);
            try {
              const user = await api<{ username: string; csrf_token: string }>(
                "/auth/login",
                { method: "POST", body: values },
              );
              setCsrf(user.csrf_token);
              done(user.username);
            } catch (e) {
              setError(e as Error);
            } finally {
              setBusy(false);
            }
          }}
        >
          <Form.Item
            name="username"
            label="用户名"
            rules={[{ required: true, message: "请输入用户名" }]}
          >
            <Input autoComplete="username" />
          </Form.Item>
          <Form.Item
            name="password"
            label="密码"
            rules={[{ required: true, message: "请输入密码" }]}
          >
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Button
            htmlType="submit"
            aria-label="登录"
            type="primary"
            block
            size="large"
            loading={busy}
          >
            登录
          </Button>
        </Form>
        <p className="muted">项目授权 · 证据追溯 · 原始产物保护</p>
      </Card>
    </main>
  );
}
function Projects() {
  const data = useData<Page<Project>>("/projects");
  if (data.loading) return <Loading />;
  if (data.error) return <ErrorBox error={data.error} />;
  return (
    <>
      <div className="page-head">
        <div>
          <span className="eyebrow">WORKSPACE</span>
          <h1>选择项目</h1>
          <p>查看构建变化，回到证据本身。</p>
        </div>
      </div>
      {!data.data?.items.length ? (
        <Blank text="尚未获授任何项目，请联系管理员分配权限。" />
      ) : (
        <div className="project-grid">
          {data.data.items.map((p) => (
            <Link key={p.key} to={`/projects/${p.key}`}>
              <Card hoverable>
                <span className="project-icon">▦</span>
                <h2>{p.name}</h2>
                <p className="muted">{p.key}</p>
                <span>打开构建工作台 →</span>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}
function useFilters() {
  const [params, set] = useSearchParams();
  const offset = Number(params.get("offset") || 0);
  const change = (values: Record<string, string>) => {
    const next = new URLSearchParams(params);
    Object.entries(values).forEach(([k, v]) =>
      v ? next.set(k, v) : next.delete(k),
    );
    set(next);
  };
  return {
    params,
    change,
    offset: Number.isInteger(offset) && offset >= 0 ? offset : 0,
  };
}
function ProjectPage() {
  const { key } = useParams();
  const navigate = useNavigate();
  const { params, change, offset } = useFilters();
  const [error, setError] = useState<Error>();
  const [busy, setBusy] = useState(false);
  const project = useData<Project>(
    `/projects/${encodeURIComponent(key || "")}`,
  );
  const history = useData<Page<History>>(
    `/projects/${encodeURIComponent(key || "")}/builds?limit=25&offset=${offset}`,
  );
  const trend = useData<{
    state: string;
    items: { build_number: string; apk_file_bytes: number | null }[];
  }>(
    `/projects/${encodeURIComponent(key || "")}/trend${params.get("group") ? "?reference_build_id=" + encodeURIComponent(params.get("group")!) : ""}`,
  );
  if (project.loading || history.loading) return <Loading />;
  if (project.error || history.error)
    return <ErrorBox error={(project.error || history.error)!} />;
  const rows = history.data?.items || [];
  const latest = rows[0];
  return (
    <>
      <div className="page-head">
        <div>
          <span className="eyebrow">PROJECT OVERVIEW</span>
          <h1>{project.data?.name}</h1>
          <p>
            <Status value={project.data?.environment || "unknown"} /> APK
            包体变化工作台
          </p>
        </div>
        <Link to={`/projects/${key}/settings`}>
          <Button>规则与审计</Button>
        </Link>
      </div>
      <div className="stats">
        <Card>
          <span className="muted">当前页最近构建 · APK 实测</span>
          <h2>
            <Bytes value={latest?.apk_file_bytes} />
          </h2>
          <span>{latest ? `构建 #${latest.build_number}` : "没有构建"}</span>
          {latest && <Status value={latest.analysis_state} />}
        </Card>
        <Card>
          <span className="muted">相对冻结基线 · APK 净增量</span>
          <h2>
            <Bytes value={latest?.delta_apk_bytes} delta />
          </h2>
          <span>无基线显示未知，已分析空差异为 0</span>
        </Card>
        <Card>
          <span className="muted">原因与告警</span>
          <h2>
            {latest?.finding_count == null ? "—" : `${latest.finding_count} 项`}
          </h2>
          <span>findings 可能重叠，不参与求和</span>
        </Card>
      </div>
      <Card
        title="同配置组趋势"
        extra={
          <Select
            aria-label="趋势配置组"
            placeholder="最近可比构建"
            style={{ minWidth: 210 }}
            value={params.get("group") || undefined}
            onChange={(v) => change({ group: v || "" })}
            allowClear
            options={rows
              .filter((r) => r.compatibility_key)
              .map((r) => ({
                value: r.build_id,
                label: `构建 #${r.build_number} · ${r.variant}`,
              }))}
          />
        }
      >
        {trend.loading ? (
          <Loading />
        ) : trend.error ? (
          <ErrorBox error={trend.error} />
        ) : trend.data?.items.length ? (
          <Chart
            label="同兼容组 APK 实测大小"
            kind="line"
            labels={trend.data.items.map((r) => `#${r.build_number}`)}
            values={trend.data.items.map((r) => r.apk_file_bytes)}
          />
        ) : (
          <Blank text="尚无硬兼容字段完整的成功分析，无法绘制同组趋势。" />
        )}
        <p className="muted">
          不同 ABI、Development、backend 与分支不连线；未分析点保留空值。
        </p>
      </Card>
      <Card title="版本比较">
        <Space wrap>
          <Select
            aria-label="基线构建"
            placeholder="选择基线"
            value={params.get("base") || undefined}
            style={{ width: 230 }}
            onChange={(v) => change({ base: v })}
            options={rows
              .filter((r) => r.analysis_revision)
              .map((r) => ({
                value: r.build_id,
                label: `#${r.build_number} · ${r.variant}`,
              }))}
          />
          <span>→</span>
          <Select
            aria-label="目标构建"
            placeholder="选择目标"
            value={params.get("target") || undefined}
            style={{ width: 230 }}
            onChange={(v) => change({ target: v })}
            options={rows
              .filter((r) => r.analysis_revision)
              .map((r) => ({
                value: r.build_id,
                label: `#${r.build_number} · ${r.variant}`,
              }))}
          />
          <Button
            type="primary"
            loading={busy}
            disabled={
              !params.get("base") ||
              !params.get("target") ||
              params.get("base") === params.get("target")
            }
            onClick={async () => {
              setBusy(true);
              setError(undefined);
              try {
                const c = await api<{ comparison_id: string }>("/comparisons", {
                  method: "POST",
                  body: {
                    base_build_id: params.get("base"),
                    target_build_id: params.get("target"),
                  },
                  headers: { "Idempotency-Key": crypto.randomUUID() },
                });
                navigate(`/comparisons/${c.comparison_id}`);
              } catch (e) {
                setError(e as Error);
              } finally {
                setBusy(false);
              }
            }}
          >
            创建比较
          </Button>
        </Space>
        {error && <ErrorBox error={error} />}
      </Card>
      <Card title="最近构建">
        <Table
          rowKey="build_id"
          pagination={false}
          scroll={{ x: 800 }}
          dataSource={rows}
          columns={[
            {
              title: "构建",
              dataIndex: "build_number",
              render: (n: string, r: History) => (
                <Link to={`/builds/${r.build_id}`}>#{n}</Link>
              ),
            },
            { title: "变体", dataIndex: "variant" },
            {
              title: "构建完成时间",
              dataIndex: "build_completed_at",
              render: (v: string | null) =>
                v ? new Date(v).toLocaleString("zh-CN") : "未记录",
            },
            {
              title: "分析状态",
              dataIndex: "analysis_state",
              render: (v: string) => <Status value={v} />,
            },
            {
              title: "APK 实测",
              dataIndex: "apk_file_bytes",
              render: (v: number | null) => <Bytes value={v} />,
            },
            {
              title: "自动比较",
              render: (_: unknown, r: History) =>
                r.comparison_id ? (
                  <Link to={`/comparisons/${r.comparison_id}`}>查看差异 →</Link>
                ) : (
                  "尚未生成"
                ),
            },
          ]}
        />
        {!rows.length && (
          <p>等待 Jenkins CLI 上传第一份构建；不会从蒲公英自动抓取 APK。</p>
        )}
        <Pager
          offset={offset}
          next={history.data?.next_offset}
          onChange={(n) => change({ offset: String(n) })}
        />
      </Card>
    </>
  );
}
function BuildPage() {
  const { id } = useParams();
  const { params, change, offset } = useFilters();
  const [refresh, setRefresh] = useState(0);
  const build = useData<Build>(`/builds/${id}`, refresh);
  const project = useData<Project>(
    build.data?.metadata
      ? `/projects/${build.data.metadata.project_key}`
      : null,
  );
  const [actionError, setActionError] = useState<Error>();
  const tab = params.get("tab") || "overview";
  const q = params.get("q") || "";
  const source =
    tab === "files"
      ? `/builds/${id}/entries?limit=25&offset=${offset}&q=${encodeURIComponent(q)}&sort=${params.get("sort") || "compressed_bytes"}${build.data?.analysis_revision ? "&revision_id=" + build.data.analysis_revision : ""}`
      : tab === "unity"
        ? `/builds/${id}/unity-assets?limit=25&offset=${offset}&q=${encodeURIComponent(q)}${build.data?.analysis_revision ? "&revision_id=" + build.data.analysis_revision : ""}`
        : null;
  const files = useData<Page<Entry>>(
    tab === "files" && build.data?.analysis_revision ? source : null,
  );
  const assets = useData<Page<UnityAsset>>(
    tab === "unity" && build.data?.analysis_revision ? source : null,
  );
  if (build.loading) return <Loading />;
  if (build.error)
    return (
      <ErrorBox error={build.error} retry={() => setRefresh((r) => r + 1)} />
    );
  const b = build.data!;
  return (
    <>
      <div className="page-head">
        <div>
          <span className="eyebrow">BUILD DETAILS</span>
          <h1>构建 #{b.metadata?.ci.build_number || b.build_id.slice(0, 8)}</h1>
          <p>
            {b.metadata && (
              <Link to={`/projects/${b.metadata.project_key}`}>
                {b.metadata.project_key}
              </Link>
            )}{" "}
            · {b.variant} <Status value={b.upload_state} />{" "}
            <Status value={b.analysis_state} />
          </p>
        </div>
        <Space>
          <Button onClick={() => setRefresh((r) => r + 1)}>刷新状态</Button>
          <Button
            disabled={project.data?.role !== "admin"}
            onClick={async () => {
              setActionError(undefined);
              try {
                await api(`/builds/${id}/pin`, {
                  method: "PUT",
                  body: { pinned: !b.pinned },
                });
                setRefresh((r) => r + 1);
              } catch (e) {
                setActionError(e as Error);
              }
            }}
          >
            {b.pinned ? "取消保留保护" : "保留保护（pin）"}
          </Button>
          <Button
            disabled={
              project.data?.role !== "admin" ||
              b.upload_state !== "SEALED" ||
              ["RUNNING", "QUEUED"].includes(b.analysis_state)
            }
            onClick={async () => {
              setActionError(undefined);
              try {
                await api(`/builds/${id}/reanalyze`, {
                  method: "POST",
                  body: {},
                  headers: { "Idempotency-Key": crypto.randomUUID() },
                });
                setRefresh((r) => r + 1);
              } catch (e) {
                setActionError(e as Error);
              }
            }}
          >
            重新分析
          </Button>
          {b.result?.comparison_id && (
            <Link to={`/comparisons/${b.result.comparison_id}`}>
              <Button type="primary">查看版本差异</Button>
            </Link>
          )}
        </Space>
      </div>
      {actionError && <ErrorBox error={actionError} />}
      {b.result &&
        ["FAILED", "RUNNING", "QUEUED"].includes(b.analysis_state) && (
          <Alert
            type="warning"
            title="本次重分析尚未成功；页面保留上一份成功修订，不代表新分析已通过。"
            description={b.jobs.map((j) => j.error_code || j.state).join(" · ")}
          />
        )}
      {!b.result && (
        <Alert
          type={b.analysis_state === "FAILED" ? "error" : "info"}
          showIcon
          title={
            b.analysis_state === "FAILED"
              ? "分析失败，未发布结果"
              : "构建尚无成功分析结果"
          }
          description={
            b.jobs.map((j) => j.error_code || j.state).join(" · ") ||
            "请完成上传并等待 Worker；未分析大小保持未知。"
          }
        />
      )}
      {b.analysis_state === "PARTIAL" && (
        <Alert
          type="warning"
          showIcon
          title="部分分析：APK 字节账可用，其他数据层存在缺口"
        />
      )}
      <Tabs
        activeKey={tab}
        onChange={(v) => change({ tab: v, offset: "0", q: "" })}
        items={[
          {
            key: "overview",
            label: "概览",
            children: (
              <>
                <div className="stats">
                  <Card title="APK 实测">
                    <Bytes value={b.result?.apk.apk_file_bytes} />
                  </Card>
                  <Card title="ZIP 条目压缩合计">
                    <Bytes value={b.result?.apk.zip_payload_bytes} />
                  </Card>
                  <Card title="容器额外开销">
                    <Bytes value={b.result?.apk.apk_overhead_bytes} />
                  </Card>
                </div>
                <Card title="构建来源">
                  <Descriptions
                    column={{ xs: 1, md: 2 }}
                    items={[
                      { key: "id", label: "Build ID", children: b.build_id },
                      {
                        key: "app",
                        label: "应用版本",
                        children: b.metadata
                          ? `${b.metadata.application_id} · ${b.metadata.version_name} (${b.metadata.version_code})`
                          : "尚未封存",
                      },
                      {
                        key: "unity",
                        label: "Unity",
                        children:
                          b.metadata?.settings.unity_version || "未采集",
                      },
                      {
                        key: "ci",
                        label: "CI",
                        children: b.metadata && (
                          <a
                            href={b.metadata.ci.build_url}
                            target="_blank"
                            rel="noreferrer"
                          >
                            原始构建记录 ↗
                          </a>
                        ),
                      },
                      {
                        key: "pgy",
                        label: "蒲公英",
                        children:
                          b.distribution.pgyer?.publish_state === "published" &&
                          b.distribution.pgyer.install_page_url ? (
                            <a
                              href={b.distribution.pgyer.install_page_url}
                              target="_blank"
                              rel="noreferrer"
                            >
                              已发布 · 安装页面 ↗
                            </a>
                          ) : (
                            b.distribution.pgyer?.publish_state ||
                            "尚未关联发布结果"
                          ),
                      },
                    ]}
                  />
                </Card>
                <Card title="原始产物（授权下载）">
                  <Table
                    rowKey="role"
                    pagination={false}
                    dataSource={b.artifacts}
                    columns={[
                      { title: "角色", dataIndex: "role" },
                      {
                        title: "大小",
                        dataIndex: "bytes",
                        render: (v: number) => <Bytes value={v} />,
                      },
                      {
                        title: "SHA-256",
                        dataIndex: "sha256",
                        render: (v: string) => (
                          <Typography.Text copyable={{ text: v }}>
                            {v.slice(0, 16)}…
                          </Typography.Text>
                        ),
                      },
                      { title: "校验", dataIndex: "state" },
                      {
                        title: "下载",
                        render: (_: unknown, r: Build["artifacts"][number]) =>
                          r.expired_at ? (
                            "原始产物已过期，索引保留"
                          ) : r.state === "verified" ? (
                            <a
                              href={`/api/v1/builds/${b.build_id}/artifacts/${r.role}/download`}
                            >
                              下载 {r.role}
                            </a>
                          ) : (
                            "等待上传"
                          ),
                      },
                    ]}
                  />
                </Card>
              </>
            ),
          },
          {
            key: "files",
            label: "APK 文件",
            children: (
              <Card>
                <Input.Search
                  key={q}
                  aria-label="APK 路径搜索"
                  placeholder="按完整或部分路径搜索"
                  defaultValue={q}
                  onSearch={(v) => change({ q: v, offset: "0" })}
                />
                <Select
                  aria-label="文件排序"
                  value={params.get("sort") || "compressed_bytes"}
                  onChange={(v) => change({ sort: v, offset: "0" })}
                  options={[
                    { value: "compressed_bytes", label: "压缩大小降序" },
                    { value: "path", label: "路径升序" },
                  ]}
                />
                {files.loading ? (
                  <Loading />
                ) : files.error ? (
                  <ErrorBox error={files.error} />
                ) : (
                  <Table
                    rowKey="entry_index"
                    pagination={false}
                    dataSource={files.data?.items}
                    columns={[
                      { title: "APK 路径", dataIndex: "normalized_path" },
                      {
                        title: "ZIP 压缩贡献",
                        dataIndex: "compressed_bytes",
                        render: (v: number) => <Bytes value={v} />,
                      },
                      {
                        title: "解压字节",
                        dataIndex: "uncompressed_bytes",
                        render: (v: number) => <Bytes value={v} />,
                      },
                    ]}
                  />
                )}
                <Pager
                  offset={offset}
                  next={files.data?.next_offset}
                  onChange={(n) => change({ offset: String(n) })}
                />
              </Card>
            ),
          },
          {
            key: "unity",
            label: "Unity 资源",
            children: (
              <Card>
                <Alert
                  type="info"
                  title="Unity packed bytes：独立序列化指标，不能与 APK 压缩增量相加。"
                />
                <Input.Search
                  key={q}
                  aria-label="Unity 资源搜索"
                  placeholder="资源路径"
                  defaultValue={q}
                  onSearch={(v) => change({ q: v, offset: "0" })}
                />
                {assets.data?.state && <Status value={assets.data.state} />}{" "}
                {assets.loading ? (
                  <Loading />
                ) : assets.error ? (
                  <ErrorBox error={assets.error} />
                ) : (
                  <Table
                    rowKey="group_key"
                    pagination={false}
                    dataSource={assets.data?.items}
                    columns={[
                      {
                        title: "资源路径",
                        dataIndex: "source_paths",
                        render: (v: string[]) =>
                          v.join(", ") || "未归属/生成资源",
                      },
                      {
                        title: "GUID",
                        dataIndex: "source_guid",
                        render: (v: string | null) => v || "无源 GUID",
                      },
                      { title: "类型", dataIndex: "asset_type" },
                      {
                        title: "Unity packed",
                        dataIndex: "packed_bytes",
                        render: (v: number) => <Bytes value={v} />,
                      },
                    ]}
                  />
                )}
                <Pager
                  offset={offset}
                  next={assets.data?.next_offset}
                  onChange={(n) => change({ offset: String(n) })}
                />
              </Card>
            ),
          },
          {
            key: "config",
            label: "构建配置",
            children: (
              <Card title="原始元数据预览（长列表有界，完整内容请下载 build_meta）">
                <Json value={b.metadata} />
              </Card>
            ),
          },
          {
            key: "coverage",
            label: "数据完整性",
            children: (
              <Card>
                <Alert
                  type="warning"
                  title="元数据为声明值，未经 Android 增强工具核验。"
                />
                <Descriptions
                  items={Object.entries(b.result?.coverage || {}).map(
                    ([k, v]) => ({
                      key: k,
                      label: k,
                      children: <Status value={v} />,
                    }),
                  )}
                />
                <h3>Unity 来源与映射覆盖</h3>
                <Json value={b.result?.unity || { state: "missing" }} />
                {b.result?.warnings?.map((w, i) => (
                  <p key={i}>{w}</p>
                ))}
              </Card>
            ),
          },
        ]}
      />
    </>
  );
}
function ComparisonPage() {
  const { id } = useParams();
  const { params, change, offset } = useFilters();
  const [tick, setTick] = useState(0);
  const data = useData<Comparison>(`/comparisons/${id}`, tick);
  const tab = params.get("tab") || "growth";
  const q = params.get("q") || "";
  const target = useData<Build>(
    data.data ? `/builds/${data.data.target_build_id}` : null,
  );
  const project = useData<Project>(
    target.data?.metadata
      ? `/projects/${target.data.metadata.project_key}`
      : null,
  );
  const query = `limit=25&offset=${offset}&q=${encodeURIComponent(q)}&sort=${params.get("sort") || "delta"}${params.get("status") ? "&status=" + encodeURIComponent(params.get("status")!) : ""}`;
  const files = useData<Page<Change>>(
    tab === "files" ? `/comparisons/${id}/changes?${query}` : null,
  );
  const unity = useData<Page<UnityChange>>(
    tab === "unity"
      ? `/comparisons/${id}/changes?layer=unity_asset&${query}`
      : null,
  );
  const findings = useData<Page<FindingItem>>(
    tab === "findings"
      ? `/comparisons/${id}/findings?limit=25&offset=${offset}&q=${encodeURIComponent(q)}${params.get("confidence") ? "&confidence=" + params.get("confidence") : ""}`
      : null,
    tick,
  );
  const [selected, setSelected] = useState<string>();
  const detail = useData<EvidenceDetail>(
    selected ? `/findings/${selected}` : null,
    tick,
  );
  const [error, setError] = useState<Error>();
  useEffect(() => {
    if (!data.data || !["QUEUED", "RUNNING"].includes(data.data.status)) return;
    const timer = setTimeout(() => setTick((v) => v + 1), 2000);
    return () => clearTimeout(timer);
  }, [data.data]);
  if (data.loading && !data.data) return <Loading />;
  if (data.error) return <ErrorBox error={data.error} />;
  const c = data.data;
  if (!c) return <Loading />;
  if (!c.result)
    return (
      <Card title="比较尚未完成">
        <Status value={c.status} />
        <p>{c.error_code || "Worker 正在计算，页面会刷新状态。"}</p>
        <Button onClick={() => setTick((v) => v + 1)}>刷新</Button>
      </Card>
    );
  const result = c.result;
  return (
    <>
      <div className="page-head">
        <div>
          <span className="eyebrow">VERSION COMPARISON</span>
          <h1>看清这次变化</h1>
          <p>
            {c.base_build_id ? (
              <Link to={`/builds/${c.base_build_id}`}>基线构建</Link>
            ) : (
              <span>无基线</span>
            )}{" "}
            → <Link to={`/builds/${c.target_build_id}`}>目标构建</Link> ·{" "}
            <Status value={c.status} />
          </p>
        </div>
        <Button onClick={() => setTick((v) => v + 1)}>刷新</Button>
      </div>
      {c.status === "NO_BASELINE" && (
        <Alert
          type="info"
          showIcon
          title="首版无基线：可以查看当前包，增量保持未知。"
        />
      )}
      {!c.comparable && c.base_build_id && (
        <Alert
          type="warning"
          showIcon
          title="跨兼容组比较：差异可查看，自动增长门禁跳过。"
        />
      )}
      <div className="stats">
        <Card title="旧 APK 实测">
          <Bytes value={result.old_apk_bytes} />
        </Card>
        <Card title="新 APK 实测">
          <Bytes value={result.new_apk_bytes} />
        </Card>
        <Card title="APK 净增量">
          <h2>
            <Bytes value={result.delta_apk_bytes} delta />
          </h2>
          <span>
            {result.growth_percent == null
              ? "增幅未知"
              : `${result.growth_percent.toFixed(2)}%`}{" "}
            · <Status value={result.quality_gate} />
          </span>
        </Card>
      </div>
      <Card>
        <Descriptions
          items={[
            {
              key: "reason",
              label: "基线选择理由",
              children: c.selection_reason,
            },
            {
              key: "git",
              label: "Git 证据",
              children: <Status value={result.git_evidence_status} />,
            },
            {
              key: "finding",
              label: "原因/告警",
              children:
                result.finding_count == null
                  ? "旧修订未执行规则"
                  : `${result.finding_count} 项（不可相加）`,
            },
          ]}
        />
        {result.warnings?.map((w, i) => (
          <p className="muted" key={i}>
            {w}
          </p>
        ))}
      </Card>
      <Tabs
        activeKey={tab}
        onChange={(v) =>
          change({ tab: v, offset: "0", q: "", status: "", sort: "" })
        }
        items={[
          {
            key: "growth",
            label: "增长来源",
            children: result.categories?.length ? (
              <>
                <Chart
                  label="APK 压缩贡献（互斥类别与容器开销）"
                  labels={result.categories.map((r) => r.category)}
                  values={result.categories.map((r) => r.delta_bytes)}
                />
                <p className="muted">
                  类别增量与容器开销合计等于 APK 净增量；findings 与 Unity
                  packed 不进入此账。
                </p>
              </>
            ) : (
              <Blank text="无可比较的物理增量；无基线不能视为零增长。" />
            ),
          },
          {
            key: "files",
            label: "文件差异",
            children: (
              <Card>
                <Space wrap>
                  <Input.Search
                    aria-label="差异路径搜索"
                    defaultValue={q}
                    placeholder="搜索 APK 路径"
                    onSearch={(v) => change({ q: v, offset: "0" })}
                  />
                  <Select
                    aria-label="差异状态"
                    allowClear
                    placeholder="全部变化状态"
                    value={params.get("status") || undefined}
                    onChange={(v) => change({ status: v || "", offset: "0" })}
                    options={[
                      "ADDED",
                      "REMOVED",
                      "MODIFIED",
                      "PACKAGING_CHANGED",
                      "UNCHANGED",
                    ].map((v) => ({ value: v, label: v }))}
                  />
                  <Select
                    aria-label="差异排序"
                    value={params.get("sort") || "delta"}
                    onChange={(v) => change({ sort: v, offset: "0" })}
                    options={[
                      { value: "delta", label: "增长优先" },
                      { value: "absolute_delta", label: "变化绝对值优先" },
                      { value: "old_bytes", label: "基线大小" },
                      { value: "new_bytes", label: "目标大小" },
                    ]}
                  />
                </Space>
                {files.loading ? (
                  <Loading />
                ) : files.error ? (
                  <ErrorBox error={files.error} />
                ) : (
                  <Table
                    rowKey="path"
                    pagination={false}
                    dataSource={files.data?.items}
                    columns={[
                      { title: "APK 路径", dataIndex: "path" },
                      { title: "状态", dataIndex: "status" },
                      ...(
                        ["old_bytes", "new_bytes", "delta_bytes"] as const
                      ).map((k) => ({
                        title: {
                          old_bytes: "旧压缩",
                          new_bytes: "新压缩",
                          delta_bytes: "压缩增量",
                        }[k],
                        dataIndex: k,
                        render: (v: number | null) => (
                          <Bytes value={v} delta={k === "delta_bytes"} />
                        ),
                      })),
                    ]}
                  />
                )}
                <Pager
                  offset={offset}
                  next={files.data?.next_offset}
                  onChange={(n) => change({ offset: String(n) })}
                />
              </Card>
            ),
          },
          {
            key: "unity",
            label: "Unity 资源",
            children: (
              <Card>
                <Alert
                  type="info"
                  title="独立 Unity packed 指标，不代表 APK 压缩贡献。"
                />
                <p>
                  资源层状态：
                  <Status value={result.unity?.status || "unavailable"} />
                  ；Importer：
                  <Status
                    value={
                      result.unity?.importer_comparison_status || "unavailable"
                    }
                  />
                </p>
                <h3>
                  Unity packed 总变化{" "}
                  <Bytes value={result.unity?.delta_packed_bytes} delta />
                </h3>
                {unity.loading ? (
                  <Loading />
                ) : unity.error ? (
                  <ErrorBox error={unity.error} />
                ) : (
                  <Table
                    rowKey="group_key"
                    pagination={false}
                    dataSource={unity.data?.items}
                    columns={[
                      {
                        title: "资源路径",
                        render: (_: unknown, row: UnityChange) => {
                          const paths = row.new_paths.length
                            ? row.new_paths
                            : row.old_paths;
                          return (
                            <div>
                              {paths.length
                                ? paths.map((path) => (
                                    <div key={path}>{path}</div>
                                  ))
                                : "未记录源路径"}
                              {row.renamed && row.old_paths.length > 0 && (
                                <div className="muted">
                                  原路径 {row.old_paths.join("、")}
                                </div>
                              )}
                              {row.asset_type && (
                                <div className="muted">{row.asset_type}</div>
                              )}
                            </div>
                          );
                        },
                      },
                      {
                        title: "源 GUID",
                        dataIndex: "source_guid",
                        render: (v: string | null) => v || "无源 GUID",
                      },
                      { title: "状态", dataIndex: "status" },
                      ...(
                        [
                          "old_packed_bytes",
                          "new_packed_bytes",
                          "delta_packed_bytes",
                        ] as const
                      ).map((k) => ({
                        title: {
                          old_packed_bytes: "旧 packed",
                          new_packed_bytes: "新 packed",
                          delta_packed_bytes: "packed 增量",
                        }[k],
                        dataIndex: k,
                        render: (v: number | null) => (
                          <Bytes value={v} delta={k === "delta_packed_bytes"} />
                        ),
                      })),
                    ]}
                  />
                )}
                <Pager
                  offset={offset}
                  next={unity.data?.next_offset}
                  onChange={(n) => change({ offset: String(n) })}
                />
              </Card>
            ),
          },
          {
            key: "config",
            label: "配置差异",
            children: (
              <Card>
                <Table
                  rowKey="field"
                  pagination={false}
                  dataSource={result.config_changes}
                  columns={[
                    { title: "字段", dataIndex: "field" },
                    {
                      title: "旧值",
                      render: (_: unknown, r: ConfigChange) =>
                        r.old_missing ? "缺失/未知" : <Json value={r.old} />,
                    },
                    {
                      title: "新值",
                      render: (_: unknown, r: ConfigChange) =>
                        r.new_missing ? "缺失/未知" : <Json value={r.new} />,
                    },
                  ]}
                />
                <p className="muted">
                  未知值不推断为默认配置；列表为有界预览，完整元数据可从两端构建下载。
                </p>
              </Card>
            ),
          },
          {
            key: "findings",
            label: "原因与证据",
            children: (
              <Card>
                <Alert
                  type="info"
                  title="这些发现可能重叠，不可求和。人工确认不会把相关性升级为确定因果。"
                />
                <Space wrap>
                  <Input.Search
                    aria-label="证据对象搜索"
                    defaultValue={q}
                    placeholder="搜索证据对象"
                    onSearch={(v) => change({ q: v, offset: "0" })}
                  />
                  <Select
                    aria-label="置信等级"
                    allowClear
                    value={params.get("confidence") || undefined}
                    placeholder="全部置信等级"
                    style={{ width: 190 }}
                    onChange={(v) =>
                      change({ confidence: v || "", offset: "0" })
                    }
                    options={[
                      { value: "observed", label: "已观测事实" },
                      { value: "correlated", label: "有证据关联" },
                      { value: "hypothesis", label: "待验证假设" },
                    ]}
                  />
                </Space>
                {findings.data?.truncated && (
                  <Alert
                    type="warning"
                    title="发现超过存储上限，仅展示前 5000 项；不能作为完整原因清单。"
                  />
                )}
                {findings.data?.rules_version !== undefined && (
                  <p className="muted">
                    冻结规则配置 v{findings.data.rules_version}
                  </p>
                )}
                {findings.loading ? (
                  <Loading />
                ) : findings.error ? (
                  <ErrorBox error={findings.error} />
                ) : (
                  findings.data?.items.map((item) => (
                    <Card key={item.id} className="finding">
                      <Space wrap>
                        <Status value={item.finding.confidence} />
                        <Status value={item.finding.review_state} />
                        <span className="muted">
                          {item.finding.rule_id} · v{item.finding.rule_version}
                        </span>
                      </Space>
                      <h3>{item.finding.title}</h3>
                      <p>{item.finding.explanation}</p>
                      <code>{item.finding.subject.key}</code>
                      <p>
                        {item.finding.metric}{" "}
                        {item.finding.delta_bytes !== null && (
                          <Bytes value={item.finding.delta_bytes} delta />
                        )}
                      </p>
                      <Button onClick={() => setSelected(item.id)}>
                        查看证据
                      </Button>
                    </Card>
                  ))
                )}
                {findings.data && !findings.data.items.length && (
                  <Blank text="当前过滤范围没有 findings；旧修订可能未执行规则，可由管理员重新分析。" />
                )}
                <Pager
                  offset={offset}
                  next={findings.data?.next_offset}
                  onChange={(n) => change({ offset: String(n) })}
                />
              </Card>
            ),
          },
        ]}
      />
      <Drawer
        title="原因与证据"
        open={!!selected}
        onClose={() => {
          setSelected(undefined);
          setError(undefined);
        }}
        width={650}
      >
        {detail.loading ? (
          <Loading />
        ) : detail.error ? (
          <ErrorBox error={detail.error} />
        ) : (
          detail.data && (
            <>
              <h2>{detail.data.finding.title}</h2>
              <Status value={detail.data.finding.confidence} />
              <Status value={detail.data.finding.review_state} />
              <p>{detail.data.finding.explanation}</p>
              {detail.data.evidence.map((e, i) => (
                <Card key={i} title={`证据 ${i + 1} · ${e.artifact_role}`}>
                  <p>{e.summary}</p>
                  <code>{e.locator}</code>
                  {e.artifact_sha256 && (
                    <Typography.Paragraph copyable>
                      {e.artifact_sha256}
                    </Typography.Paragraph>
                  )}
                  {e.sources.map((s, n) => (
                    <section key={n}>
                      <Link to={`/builds/${s.build_id}`}>
                        来源构建 {s.build_id.slice(0, 8)}
                      </Link>{" "}
                      · {s.state}
                      {s.download_url && (
                        <>
                          {" "}
                          · <a href={s.download_url}>下载原始证据</a>
                        </>
                      )}
                      <Json value={s.value} />
                    </section>
                  ))}
                </Card>
              ))}
              <h3>待验证事项</h3>
              {detail.data.finding.suggested_checks.map((s, i) => (
                <p key={i}>{s}</p>
              ))}
              {error && <ErrorBox error={error} />}
              <Space>
                {["confirmed", "rejected", "unreviewed"].map((state) => (
                  <Button
                    key={state}
                    disabled={project.data?.role !== "admin"}
                    onClick={async () => {
                      try {
                        await api(`/findings/${selected}/review`, {
                          method: "PUT",
                          body: { review_state: state },
                          headers: {
                            "If-Match": `"${detail.data!.review_version}"`,
                          },
                        });
                        setTick((v) => v + 1);
                      } catch (e) {
                        setError(e as Error);
                      }
                    }}
                  >
                    {
                      (
                        {
                          confirmed: "确认发现",
                          rejected: "拒绝发现",
                          unreviewed: "恢复未复核",
                        } as Record<string, string>
                      )[state]
                    }
                  </Button>
                ))}
              </Space>
              <p className="muted">仅项目管理员可以复核，操作保留审计记录。</p>
            </>
          )
        )}
      </Drawer>
    </>
  );
}
function SettingsPage() {
  const { key } = useParams();
  const { offset, change } = useFilters();
  const [tick, setTick] = useState(0);
  const data = useData<Rules>(`/projects/${key}/rules`, tick);
  const project = useData<Project>(`/projects/${key}`);
  const [error, setError] = useState<Error>();
  const [saved, setSaved] = useState(false);
  const audit = useData<
    Page<{ id: string; action: string; actor: string; created_at: string }>
  >(
    project.data?.role === "admin"
      ? `/projects/${key}/audit?limit=25&offset=${offset}`
      : null,
    tick,
  );
  if (data.loading || project.loading) return <Loading />;
  if (data.error || project.error)
    return <ErrorBox error={(data.error || project.error)!} />;
  const r = data.data!;
  return (
    <>
      <div className="page-head">
        <div>
          <span className="eyebrow">PROJECT SETTINGS</span>
          <h1>规则与审计</h1>
          <p>
            <Link to={`/projects/${key}`}>{project.data?.name}</Link> · 配置版本{" "}
            {r.version}
          </p>
        </div>
      </div>
      {project.data?.role !== "admin" && (
        <Alert
          type="warning"
          title="只读：只有项目管理员可修改规则及查看审计。"
        />
      )}
      {error && (
        <ErrorBox
          error={error}
          retry={() => {
            setError(undefined);
            setTick((v) => v + 1);
          }}
        />
      )}
      {saved && (
        <Alert type="success" title="配置已保存；旧比较保留其冻结版本。" />
      )}
      <Card title="保留策略（运行配置）">
        <p>
          APK：{project.data?.retention_days?.apk} 天；JSON：
          {project.data?.retention_days?.reports} 天；派生指标：
          {project.data?.retention_days?.metrics} 天。
        </p>
        <p>{project.data?.raw_protection}</p>
        <p className="muted">
          自动清理默认关闭。管理员先生成审计计划再应用；pin 不能恢复已过期文件。
        </p>
      </Card>
      <Card title="advisory 阈值与基线">
        <Form
          key={r.version}
          layout="vertical"
          disabled={project.data?.role !== "admin"}
          initialValues={{
            ...r.settings,
            growth_percent: r.settings.growth_basis_points / 100,
          }}
          onFinish={async (
            values: Rules["settings"] & { growth_percent: number },
          ) => {
            setError(undefined);
            setSaved(false);
            try {
              await api(`/projects/${key}/rules`, {
                method: "PUT",
                headers: { "If-Match": `"${r.version}"` },
                body: {
                  growth_bytes: values.growth_bytes,
                  growth_basis_points: Math.round(values.growth_percent * 100),
                  absolute_budget_bytes: values.absolute_budget_bytes ?? null,
                  fixed_baseline_id: values.fixed_baseline_id || null,
                },
              });
              setSaved(true);
              setTick((v) => v + 1);
            } catch (e) {
              setError(e as Error);
            }
          }}
        >
          <Form.Item
            name="absolute_budget_bytes"
            label="APK 绝对预算（整数 bytes，留空表示未配置）"
          >
            <InputNumber
              aria-label="APK 绝对预算"
              min={0}
              max={Number.MAX_SAFE_INTEGER}
              precision={0}
              style={{ width: "100%" }}
            />
          </Form.Item>
          <Form.Item
            name="growth_bytes"
            label="净增长阈值（整数 bytes，5 MiB = 5242880）"
            rules={[{ required: true }]}
          >
            <InputNumber
              aria-label="净增长阈值"
              min={0}
              max={Number.MAX_SAFE_INTEGER}
              precision={0}
              style={{ width: "100%" }}
            />
          </Form.Item>
          <Form.Item
            name="growth_percent"
            label="增幅阈值（%，与字节阈值同时满足）"
            rules={[{ required: true }]}
          >
            <InputNumber
              aria-label="增幅阈值"
              min={0}
              max={10000}
              precision={2}
              style={{ width: "100%" }}
            />
          </Form.Item>
          <Form.Item
            name="fixed_baseline_id"
            label="固定基线 Build ID（留空自动选择）"
          >
            <Input aria-label="固定基线" />
          </Form.Item>
          <Button htmlType="submit" type="primary">
            保存规则
          </Button>
        </Form>
        <p className="muted">
          配置影响新比较，不改变已发布报告。跨组和缺少基线时跳过增长门禁；绝对预算独立评估。
        </p>
      </Card>
      {project.data?.role === "admin" && (
        <Card title="最近审计">
          {audit.error ? (
            <ErrorBox error={audit.error} />
          ) : (
            <Table
              rowKey="id"
              pagination={false}
              dataSource={audit.data?.items}
              columns={[
                { title: "操作", dataIndex: "action" },
                { title: "操作人标识", dataIndex: "actor" },
                { title: "UTC 时间", dataIndex: "created_at" },
              ]}
            />
          )}
          <Pager
            offset={offset}
            next={audit.data?.next_offset}
            onChange={(n) => change({ offset: String(n) })}
          />
        </Card>
      )}
    </>
  );
}
export default function App() {
  const [user, setUser] = useState<string | null>();
  const [error, setError] = useState<Error>();
  const navigate = useNavigate();
  useEffect(() => {
    let active = true;
    api<{ username: string; csrf_token: string }>("/auth/me")
      .then((u) => {
        if (active) {
          setCsrf(u.csrf_token);
          setUser(u.username);
        }
      })
      .catch((e) => {
        if (active) {
          if (e.status === 401) setUser(null);
          else setError(e);
        }
      });
    return () => {
      active = false;
    };
  }, []);
  if (error)
    return (
      <main className="content">
        <ErrorBox error={error} retry={() => location.reload()} />
      </main>
    );
  if (user === undefined) return <Loading />;
  if (user === null)
    return (
      <Login
        done={(name) => {
          setUser(name);
          navigate("/projects");
        }}
      />
    );
  return (
    <div className="shell">
      <aside>
        <Link className="brand" to="/projects">
          ▦ BuildSize<small>INSPECTOR</small>
        </Link>
        <p className="sidebar-label">工作台</p>
        <Link className="nav-link" to="/projects">
          ▦ 项目与构建
        </Link>
        <div className="sidebar-note">
          让每一份增长，
          <br />
          都回到原始证据。<p>APK / UNITY / GIT</p>
        </div>
      </aside>
      <section className="body">
        <header>
          <span>
            构建体积分析平台 <span className="header-separator">/</span>{" "}
            BuildSize Inspector
          </span>
          <Space>
            <span>{user}</span>
            <Button
              onClick={async () => {
                try {
                  await api("/auth/logout", { method: "POST" });
                  setCsrf("");
                  setUser(null);
                  navigate("/login");
                } catch (e) {
                  setError(e as Error);
                }
              }}
            >
              退出登录
            </Button>
          </Space>
        </header>
        <main className="content">
          <Routes>
            <Route path="/projects" element={<Projects />} />
            <Route path="/projects/:key" element={<ProjectPage />} />
            <Route path="/projects/:key/settings" element={<SettingsPage />} />
            <Route path="/builds/:id" element={<BuildPage />} />
            <Route path="/comparisons/:id" element={<ComparisonPage />} />
            <Route path="*" element={<Navigate to="/projects" replace />} />
          </Routes>
          <footer>整数 bytes 存储 · MiB 展示 · 所有结论保留证据边界</footer>
        </main>
      </section>
    </div>
  );
}
