import type { Metadata } from "next";

/**
 * 接口文档（中文说明书）。
 *
 * 为什么不是直接把 Swagger UI 嵌进来：
 * - Swagger 的排版对非后端读者是"乱格式"——它就是一堆 JSON schema 折叠树；
 * - 也没有一句话说清楚「这个系统有哪些动作、每个动作干什么、出错是什么码」。
 *
 * 所以这一页是**人写的中文说明**：先讲通用约定，再按业务分组列出 31 个端点，
 * 最后给 SSE 事件、错误码、权限点三张表。机器可读的原始定义仍然放在 `/openapi.json`，
 * 可交互调试仍然走 `/docs`，两者都在本页顶部给出入口。
 *
 * 内容是静态的：写死在这里比运行时解析 OpenAPI 更稳（后端加字段不会让本页 500），
 * 代价是新增端点要手动补一行——`docs/plans/PyKnora_前端DAG执行路径.md` 里记了这条约定。
 */

export const metadata: Metadata = {
  title: "接口文档 · PyKnora",
};

type Endpoint = {
  readonly method: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  readonly path: string;
  readonly summary: string;
  readonly note?: string;
};

type Group = {
  readonly title: string;
  readonly intro: string;
  readonly endpoints: readonly Endpoint[];
};

const GROUPS: readonly Group[] = [
  {
    title: "认证",
    intro: "无状态 JWT（HS256）。登录/注册直接返回 token，没有单独的「刷新」端点。",
    endpoints: [
      { method: "POST", path: "/auth/login", summary: "用用户名密码换 token" },
      {
        method: "POST",
        path: "/auth/register",
        summary: "注册并直接签发 token",
        note: "email 必填；用户名重复返回 409 CONFLICT",
      },
      { method: "GET", path: "/auth/me", summary: "当前身份、角色与权限点" },
    ],
  },
  {
    title: "知识库",
    intro: "嵌入模型与切片参数在创建时固化，之后只能改名。写操作一律要 If-Match。",
    endpoints: [
      { method: "GET", path: "/knowledge-bases", summary: "列表（游标分页，无总数）" },
      { method: "POST", path: "/knowledge-bases", summary: "新建（201，固化索引参数）" },
      { method: "GET", path: "/knowledge-bases/{kb_id}", summary: "详情" },
      {
        method: "PATCH",
        path: "/knowledge-bases/{kb_id}",
        summary: "改名",
        note: "If-Match 传 row_version；缺头 428，不一致 409",
      },
      {
        method: "DELETE",
        path: "/knowledge-bases/{kb_id}",
        summary: "删除（202，后台异步回收）",
        note: "立即阻断可见性，切片由后台 Worker 异步清理",
      },
    ],
  },
  {
    title: "文档",
    intro: "上传是异步的：先 202 受理并返回 job_id，摄取完成后文档才可检索。",
    endpoints: [
      {
        method: "GET",
        path: "/knowledge-bases/{kb_id}/documents",
        summary: "知识库下的文档列表",
      },
      {
        method: "POST",
        path: "/knowledge-bases/{kb_id}/documents",
        summary: "上传（multipart，hash 三分支）",
        note: "内容重复返回 200 且 deduped=true，不建新版本也不入队",
      },
      { method: "GET", path: "/documents/{document_id}", summary: "详情（含活动索引指针）" },
      {
        method: "GET",
        path: "/documents/{document_id}/chunks",
        summary: "当前活动构建的切片",
        note: "total 是页内条数，不是全量",
      },
      { method: "DELETE", path: "/documents/{document_id}", summary: "删除（先阻断可见性再清理）" },
      { method: "GET", path: "/documents/{document_id}/lineage", summary: "版本链路（含创建者）" },
    ],
  },
  {
    title: "作业",
    intro: "上传、发布、清理都产生作业。六个状态：pending/processing/succeeded/failed/canceled/superseded。",
    endpoints: [
      {
        method: "GET",
        path: "/jobs/{job_id}",
        summary: "作业状态",
        note: "terminal 对 superseded 恒为 false，判终态要连 status 一起看",
      },
      { method: "POST", path: "/jobs/{job_id}/cancel", summary: "取消作业（排队中立即取消）" },
    ],
  },
  {
    title: "检索",
    intro: "纯检索，不调用模型：返回命中的切片与两路排名，适合调参与核对召回。",
    endpoints: [
      {
        method: "POST",
        path: "/knowledge-bases/{kb_id}/query",
        summary: "混合检索（向量 + 关键词，RRF 融合）",
      },
    ],
  },
  {
    title: "问答",
    intro: "检索 + 生成，SSE 流式返回。帧顺序：retrieval → token* → done；出错则先 error 再补一帧 done。",
    endpoints: [
      {
        method: "POST",
        path: "/knowledge-bases/{kb_id}/chat",
        summary: "流式 RAG 问答",
        note: "建流之前能判的错（缺 API key、缺 embedder）直接 503，不进流",
      },
    ],
  },
  {
    title: "成员与角色",
    intro:
      "没有邀请制：加成员 = 按已存在的 principal_id 直接建成员关系。角色是数据（可新建），不是固定枚举。",
    endpoints: [
      { method: "GET", path: "/workspaces/{ws_id}/members", summary: "成员列表（只有 user_id）" },
      { method: "POST", path: "/workspaces/{ws_id}/members", summary: "加成员（200，幂等覆盖角色）" },
      { method: "PATCH", path: "/workspaces/{ws_id}/members/{id}", summary: "改角色" },
      { method: "DELETE", path: "/workspaces/{ws_id}/members/{id}", summary: "移除成员 / 主动退出" },
      {
        method: "GET",
        path: "/workspaces/{ws_id}/members/{id}/permissions",
        summary: "某成员实际持有的权限点（诊断）",
      },
      { method: "PUT", path: "/workspaces/{ws_id}/owner", summary: "转移所有权" },
      { method: "GET", path: "/workspaces/{ws_id}/permissions", summary: "我在本工作区的权限点" },
      { method: "GET", path: "/workspaces/{ws_id}/roles", summary: "角色与权限点目录" },
      { method: "POST", path: "/workspaces/{ws_id}/roles", summary: "新建角色" },
      { method: "DELETE", path: "/workspaces/{ws_id}/roles/{role_key}", summary: "删除角色（有人用则拒绝）" },
      {
        method: "PUT",
        path: "/workspaces/{ws_id}/roles/{role_key}/permissions",
        summary: "整组替换角色的权限点",
      },
    ],
  },
  {
    title: "运维与元信息",
    intro: "重建索引与健康检查。后者不查库，只说明进程在不在。",
    endpoints: [
      { method: "POST", path: "/admin/reindex", summary: "按当前 profile 重建索引（需 index.rebuild）" },
      { method: "GET", path: "/health", summary: "存活探针（不查数据库）" },
    ],
  },
];

const CONVENTIONS: readonly { readonly term: string; readonly detail: string }[] = [
  {
    term: "基址",
    detail: "所有业务接口都在 /api/v1 下。浏览器同源访问 /api/v1/*，由前端 dev server 转发到后端。",
  },
  {
    term: "鉴权",
    detail: "请求头 Authorization: Bearer <token>。令牌无状态，退出登录只是前端丢弃它。",
  },
  {
    term: "工作区",
    detail:
      "请求头 X-Workspace-ID 指明工作区；省略时用身份里的 default_workspace_id。身份字段只从令牌来，请求体里的同名参数一律不读。",
  },
  {
    term: "并发控制",
    detail: "写操作带 If-Match: <row_version>（裸数字，不要加引号）。缺头 428 REVISION_MISSING，不匹配 409 REVISION_CONFLICT。",
  },
  {
    term: "错误体",
    detail: '{"error":{"code","message","retryable","details"},"request_id":"..."}。404 不区分「不存在」与「无权访问」。',
  },
  {
    term: "时间",
    detail: "一律 UTC ISO-8601；界面按 Asia/Shanghai 渲染。",
  },
];

const SSE_EVENTS: readonly { readonly name: string; readonly meaning: string }[] = [
  { name: "retrieval", meaning: "本次检索用了哪些切片（含两路排名与融合分）" },
  { name: "token", meaning: "生成的一段文本；多帧拼接才是完整回答" },
  {
    name: "done",
    meaning: "结束帧：run_id、prompt_version、令牌数、引用校验结论（invalid_citations 是条数）",
  },
  { name: "error", meaning: "流内错误（超时、上游拒绝、预算中止）；之后必定还有一帧 done" },
];

const ERROR_CODES: readonly { readonly code: string; readonly meaning: string }[] = [
  { code: "UNAUTHENTICATED", meaning: "没有令牌或令牌无效" },
  { code: "FORBIDDEN", meaning: "有身份但缺权限点" },
  { code: "RESOURCE_NOT_FOUND", meaning: "对象不存在，或存在但无权访问" },
  { code: "INVALID_REQUEST", meaning: "参数不合法（含 If-Match 格式错）" },
  { code: "CONFLICT", meaning: "与现有资源冲突（如用户名已被占用）" },
  { code: "IDEMPOTENCY_CONFLICT", meaning: "同一 Idempotency-Key 送了不同内容" },
  { code: "REVISION_MISSING", meaning: "写操作缺 If-Match" },
  { code: "REVISION_CONFLICT", meaning: "If-Match 与当前版本不一致" },
  { code: "UNSUPPORTED_FORMAT", meaning: "文件类型不支持" },
  { code: "PARSE_FAILED", meaning: "解析失败（扫描件、损坏文件）" },
  { code: "PARSE_INCOMPLETE", meaning: "解析不完整（可检索但已标记）" },
  { code: "INDEX_INCOMPLETE", meaning: "索引没有覆盖全部切片" },
  { code: "STALE_ATTEMPT", meaning: "作业的租约过期，已换人执行" },
  { code: "VERSION_SUPERSEDED", meaning: "同一文档已有更新的版本，本次被取代" },
  { code: "CURSOR_EXPIRED", meaning: "分页游标过期或与当前身份不符" },
  { code: "DEPENDENCY_UNAVAILABLE", meaning: "依赖不可用（数据库、嵌入服务）" },
  { code: "INTERNAL_ERROR", meaning: "未分类的内部错误，可带 request_id 反馈" },
];

const PERMISSIONS: readonly { readonly code: string; readonly meaning: string }[] = [
  { code: "knowledge_base.read", meaning: "看知识库与详情" },
  { code: "knowledge_base.write", meaning: "建库与改名" },
  { code: "knowledge_base.delete", meaning: "删库" },
  { code: "document.read", meaning: "看文档与切片" },
  { code: "document.write", meaning: "上传文档" },
  { code: "document.delete", meaning: "删文档" },
  { code: "chunk.read", meaning: "读切片正文" },
  { code: "job.read", meaning: "看作业状态" },
  { code: "job.cancel", meaning: "取消作业" },
  { code: "query.run", meaning: "跑检索" },
  { code: "chat.run", meaning: "跑流式问答" },
  { code: "lineage.read", meaning: "看版本链路" },
  { code: "index.rebuild", meaning: "重建索引" },
  { code: "workspace.manage_members", meaning: "管成员" },
  { code: "workspace.manage_roles", meaning: "管角色" },
  { code: "platform.manage_roles", meaning: "平台级角色矩阵（当前无人持有）" },
];

const METHOD_TONE: Readonly<Record<Endpoint["method"], string>> = {
  GET: "border-info/30 bg-info/10 text-info",
  POST: "border-success/30 bg-success/10 text-success",
  PATCH: "border-warning/30 bg-warning/10 text-warning",
  PUT: "border-warning/30 bg-warning/10 text-warning",
  DELETE: "border-danger/30 bg-danger/10 text-danger",
};

export default function ApiDocsPage() {
  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-2">
        <h1 className="text-xl font-semibold text-fg">接口文档</h1>
        <p className="max-w-3xl text-sm text-fg-muted">
          这一页是给人读的中文说明书：系统有哪些动作、每个动作干什么、出错是什么码。
          机器可读的原始定义在{" "}
          <a className="text-primary underline underline-offset-4" href="/openapi.json">
            /openapi.json
          </a>
          ，可交互调试在{" "}
          <a className="text-primary underline underline-offset-4" href="/docs">
            /docs
          </a>
          （Swagger UI；它的 JS/CSS 由后端自托管，不走 CDN，断网也能打开）。
        </p>
      </header>

      <section className="flex flex-col gap-3">
        <h2 className="text-base font-semibold text-fg">通用约定</h2>
        <dl className="grid gap-x-6 gap-y-2 rounded-lg border border-border bg-surface p-4 text-sm sm:grid-cols-[7rem_1fr]">
          {CONVENTIONS.map((item) => (
            <div key={item.term} className="contents">
              <dt className="font-medium text-fg">{item.term}</dt>
              <dd className="text-fg-muted">{item.detail}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-base font-semibold text-fg">端点清单（31 个）</h2>
        <p className="text-sm text-fg-muted">
          路径都省略了 <code className="font-mono text-fg">/api/v1</code> 前缀（除 /health）。
        </p>
        {GROUPS.map((group) => (
          <div key={group.title} className="flex flex-col gap-2">
            <h3 className="text-sm font-semibold text-fg">
              {group.title}
              <span className="ml-2 font-normal text-fg-subtle">{group.endpoints.length} 个</span>
            </h3>
            <p className="text-xs text-fg-muted">{group.intro}</p>
            <div className="overflow-x-auto rounded-lg border border-border bg-surface">
              <table className="w-full min-w-[42rem] border-collapse text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-xs text-fg-muted">
                    <th className="px-3 py-2 font-medium">方法</th>
                    <th className="px-3 py-2 font-medium">路径</th>
                    <th className="px-3 py-2 font-medium">说明</th>
                  </tr>
                </thead>
                <tbody>
                  {group.endpoints.map((endpoint) => (
                    <tr key={`${endpoint.method} ${endpoint.path}`} className="border-b border-border last:border-0">
                      <td className="px-3 py-2 align-top">
                        <span
                          className={`inline-flex h-6 items-center rounded border px-1.5 font-mono text-xs ${METHOD_TONE[endpoint.method]}`}
                        >
                          {endpoint.method}
                        </span>
                      </td>
                      <td className="px-3 py-2 align-top font-mono text-xs text-fg">{endpoint.path}</td>
                      <td className="px-3 py-2 align-top text-fg-muted">
                        {endpoint.summary}
                        {endpoint.note ? (
                          <span className="mt-0.5 block text-xs text-fg-subtle">{endpoint.note}</span>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))}
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-base font-semibold text-fg">问答的流式事件</h2>
        <ul className="flex flex-col gap-1.5 rounded-lg border border-border bg-surface p-4 text-sm">
          {SSE_EVENTS.map((event) => (
            <li key={event.name} className="flex flex-wrap gap-x-3">
              <code className="min-w-[5.5rem] font-mono text-xs text-primary">{event.name}</code>
              <span className="text-fg-muted">{event.meaning}</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-base font-semibold text-fg">错误码（17 个）</h2>
        <div className="grid gap-x-6 gap-y-1.5 rounded-lg border border-border bg-surface p-4 text-sm sm:grid-cols-[14rem_1fr]">
          {ERROR_CODES.map((item) => (
            <div key={item.code} className="contents">
              <code className="font-mono text-xs text-fg">{item.code}</code>
              <span className="text-fg-muted">{item.meaning}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-base font-semibold text-fg">权限点（16 个）</h2>
        <p className="text-sm text-fg-muted">
          角色只是权限点的集合；成员页能看到每个角色实际持有哪些。
        </p>
        <div className="grid gap-x-6 gap-y-1.5 rounded-lg border border-border bg-surface p-4 text-sm sm:grid-cols-[15rem_1fr]">
          {PERMISSIONS.map((item) => (
            <div key={item.code} className="contents">
              <code className="font-mono text-xs text-fg">{item.code}</code>
              <span className="text-fg-muted">{item.meaning}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
