# PyKnora Web 前端

PyKnora 知识库系统的 Web 客户端：登录 → 建知识库 → 上传文档 → 看切片/作业 → 检索调试 → 流式问答。

**技术栈**：Next.js 16（App Router）+ React 19 + TypeScript 5.9 + TanStack Query v5 + Tailwind CSS v4 + 自研组件库（shadcn 风格，`components.json` 已就位）+ Vitest 3 + MSW 2 + Playwright 1.56 + axe-core。

**这份文档只写跑得通的命令**。本文末尾「已知缺口」列出**没做/没验**的东西，不粉饰。

---

## 1. 快速开始

### 1.1 先起后端（本仓库根目录）

```bash
# 依赖
uv sync --extra dev

# 数据库（宿主端口按 .env 的 PYKNORA_DATABASE_URL；本项目常用 5434 的容器 pyknora-pg-p0）
docker start pyknora-pg-p0     # 首次创建用 AGENTS.md §6 的 docker run 命令

# 建表 + 造数
uv run python scripts/migrate.py
uv run python scripts/seed_for_m3.py

# API（前端开发期默认代理到 8010）
uv run uvicorn pyknora.api.main:create_app --factory --host 127.0.0.1 --port 8010
```

> `-p`/`--port` 与 `web/next.config.ts` 的 `API_PROXY_TARGET` 必须一致。默认 `http://127.0.0.1:8010`，可用环境变量覆盖。

### 1.2 再起前端

```bash
cd web
pnpm install
pnpm dev            # http://127.0.0.1:3000
```

浏览器只访问 **3000**：`next.config.ts` 把 `/api/*`、`/health`、`/openapi.json` 同源转发给 FastAPI。这样 Bearer 令牌留 localStorage 也不会触发跨域预检，后端不必放宽 `allow_origins`。

**后端不在 8010 时**：

```bash
# PowerShell
$env:API_PROXY_TARGET="http://127.0.0.1:8000"; pnpm dev
```

### 1.3 首个账号

前端没有"造账号"能力，直接打后端：

```bash
curl -X POST http://127.0.0.1:8010/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"demo","email":"demo@example.com","password":"demo1234"}'
```

`register` 直接返回 `TokenResponse`（201），前端拿到就能进 `/kbs`。注意 **`email` 必填**、密码有长度下限（以 `src/pyknora/schemas/auth.py` 为准）。

---

## 2. 目录结构

```text
web/
├── app/
│   ├── layout.tsx                    根布局（字体、Providers）
│   ├── globals.css                   Tailwind v4 + design tokens
│   ├── (auth)/login/page.tsx         登录/注册
│   └── (app)/                        已登录区（侧栏壳）
│       ├── layout.tsx                AppShell + AuthGuard
│       └── kbs/
│           ├── page.tsx              知识库列表
│           └── [kbId]/
│               ├── layout.tsx        导出 useKbId()（不渲染 <main>）
│               ├── documents/page.tsx
│               ├── documents/[documentId]/page.tsx
│               ├── query/page.tsx
│               └── chat/page.tsx
├── components/
│   ├── ui/                           17 个基础组件（按钮/表单/表格/对话框…）
│   └── app-shell/                    侧栏导航与布局
├── features/                         按域切分，每个域自带 api/hooks/错误映射/组件
│   ├── auth/  kbs/  documents/  jobs/  query/  chat/
├── lib/
│   ├── api/client.ts                 fetch 封装：ApiError + 幂等键 + If-Match
│   ├── api/hooks.ts                  qk 查询键工厂（第 2 段恒为 workspaceId）
│   ├── contracts/                    zod schema + 生成的 openapi.d.ts
│   └── auth/                         令牌存取、identity 缓存、usePermissions
├── contracts/openapi.json            后端 OpenAPI 快照（契约测试与类型生成的数据源）
└── tests/
    ├── contract/                     契约测试（对 openapi.json 断言）
    ├── unit/                         单测（组件 + hooks + 错误映射）
    ├── msw/                          MSW handlers 与 fixtures
    └── e2e/                          Playwright（**见已知缺口**）
```

---

## 3. 脚本

| 命令 | 作用 |
| --- | --- |
| `pnpm dev` | 开发服务器（3000） |
| `pnpm build` / `pnpm start` | 生产构建 / 生产启动 |
| `pnpm typecheck` | `tsc --noEmit`，**必须 0 错** |
| `pnpm test` | Vitest 全量（单测 + 契约测试） |
| `pnpm test:watch` | Vitest watch |
| `pnpm test:e2e` | Playwright（需先满足 §5 的前置） |
| `pnpm gen:contracts` | 从 `contracts/openapi.json` 重新生成 `lib/contracts/openapi.d.ts` |
| `pnpm check` | `typecheck && test`（提交前自查用） |

---

## 4. 契约口径：与后端对齐时必须知道的 8 条

这些**不是风格偏好**，是当前后端实现的真实行为，写错会静默出错：

1. **作业六态**：`pending / processing / succeeded / failed / cancelled / superseded`。后端 `terminal` 对 `superseded` **恒为 false**，所以轮询停止条件是 `terminal === true || status === "superseded"`（统一用 `isJobTerminal()`）。
2. **`ChunkPage.total` 是"本页条数"不是总数**；只有 `DocumentPage.total` 是全量计数；知识库列表是游标分页、**没有 total**。
3. **成员列表键是 `members`**，无分页；`POST /workspaces/{ws}/members` 只收**已存在**的 `{user_id, role}` —— **没有邀请制**，也**不回用户名**。
4. **`DocumentDetail` 没有 `created_at`**；`filename` 来自 `coalesce(desired_document_version_id, active_document_version_id)` 指向的版本对象键末段，两个指针都空才是 `null`。
5. **`lineage` 只有版本链**（`versions[] + is_active + contributors`），**没有发布事件字段**。
6. **`done.invalid_citations` 是整数条数**，不是列表；越界条目由前端用 `\[资料\s*(\d+)\]` 反算。
7. **SSE 顺序**：`retrieval` → `token*` → `done`；`error` 之后**必定**还有一帧 `done`。**流式端点不会用 HTTP 状态码报错**（流已开始就改不了状态码），失败一律降级成 `error` 事件。
8. **`If-Match` 传裸数字**（`String(rowVersion)`），后端自己 strip 引号与 `W/`；**缺头返回 428 `REVISION_MISSING`**。

错误体统一是 `{"error":{code,message,retryable,details},"request_id":"..."}`，前端用 `ApiError` 承载，`code` 取自后端的 17 个 `ErrorCode`。

---

## 5. 测试

```bash
cd web
pnpm typecheck                        # → 0 错
pnpm exec vitest run                  # 单测 + 契约测
```

**当前基线（2026-11-01 实测）**：`pnpm typecheck` → `TYPECHECK_EXIT=0`；`pnpm exec vitest run` → **28 files / 212 tests passed**；`pnpm build` → exit 0，产出 8 条路由。

### e2e 的前置（尚未跑通）

`playwright.config.ts` 会一次拉起四个进程：FastAPI(8000) + 假 LLM 上游(8123) + Next(3000) + worker。跑之前需要：

- `docker start pyknora-pg-p0`（5434 上的 pyknora 库）
- `pnpm exec playwright install chromium`（本机已装则可跳过）
- 空隙端口 3000 / 8000 / 8123（`reuseExistingServer: !CI`，被占会复用旧进程污染断言）

```bash
pnpm exec playwright test --reporter=list
```

---

## 6. 已知缺口（诚实清单）

> 2026-11-02 收尾轮已补齐前两行；保留下来的都是**明确没做**的，不写成"基本可用"。

| 缺口 | 现状 | 影响 |
| --- | --- | --- |
| **移动端窄屏** | e2e 只有 `desktop` 1440×900 项目（原 `mobile` 项目已删） | 底部 Tab 布局、窄屏表格横向滚动无自动化覆盖 |
| **无障碍只扫默认态** | axe 扫 5 个路由（`/login`、`/kbs`、`documents`、`query`、`chat`）**默认态**，全 0 违规 | 错误态、空态、对话框展开态、文档详情页；聊天"回答已渲染"状态（流式期间 DOM 每 60ms 变，不可复现）未扫 |
| **键盘与读屏** | 未做键盘遍历测试与真实读屏器实测 | 焦点顺序、`aria-live` 播报质量未验 |
| **宽屏并排布局** | `use-wide-layout` 已有实现，**无自动化用例** | 宽屏下聊天+引用并排的实际观感未验 |
| **真实 LLM** | 单测/契约测全走 MSW；e2e 用假 OpenAI SSE（8123） | 真实模型的流式抖动、拒答、限流分支未联调 |
| **jsdom 跨 realm** | `AbortSignal` / `FormData` 在 jsdom 与 undici 之间不是同一 realm | 请求函数**不透传 `signal`**、multipart 的 `Content-Type` 在单测里不可断言 —— 这两项只能靠真浏览器补（e2e 已覆盖） |
| **后端的写后读窗口（P1-1）** | 建库 201 后立刻上传偶发 `404 knowledge base not found`（约 8 次 1 次） | 推断是 `get_session` 的 commit 排在响应之后；e2e 侧退避重试 3 次并打 `[e2e][warn]`，**没有掩盖**。属后端待修 |

---

## 6.1 已修的历史缺陷（留档，避免回归）

| 缺陷 | 发现方式 | 修法 |
| --- | --- | --- |
| 业务请求**不带 `Authorization`** → 真浏览器列表永远空白（显示「登录状态已失效」） | 只有 e2e 真打后端才暴露；MSW 不校验鉴权，250 条单测全绿 | `lib/api/client.ts` 三档 token 语义：**省略**=用 `lib/auth/token-store` 会话令牌、**`null`**=强制匿名、**字符串**=覆盖 |
| 对比度不达 AA：`--color-fg-subtle` 3.36:1（10–12px 正文用了 11 处） | axe `color-contrast` serious ×5 路由 | 压暗到 `#5f7175`（5.12:1 / 4.76:1） |
| 改名按钮可见「改名」但 `aria-label="重命名「X」"` | axe `label-content-name-mismatch` serious ×2 | 无障碍名以可见词开头 |
| `vitest.setup.ts` 在 Node 26 下 `window.localStorage.clear()` 必抛 | 每个用例 afterEach 炸 | 回落到 `sessionStorage` + 可选调用 |

---

## 7. 相关文档

- `docs/plans/PyKnora_前端实施计划.md` —— 需求边界、页面清单、验收标准
- `docs/plans/PyKnora_前端DAG执行路径.md` —— 12 个节点、波次、写范围、**执行结果（§7）**
- `docs/IMPLEMENTATION_STATUS.md` —— 每轮改动与真实命令输出
- `AGENTS.md` —— 仓库流程纪律（进度更新、门禁、代码红线）
