# PyKnora 前端 DAG 执行路径

> 依据：`docs/plans/PyKnora_前端实施计划.md`（P0 审定版，190 行）
> 目的：把计划 §7 的 F0–F6 拆成**可并行的节点**，每个节点有唯一写入范围、依赖、验收命令。
> 纪律：**非单节点阶段禁止串行**——同一波次内所有无依赖节点必须同时开工；串行只允许出现在「唯一的写入者」节点上。

---

## 1. 编排硬规则

| # | 规则 | 原因 |
| --- | --- | --- |
| R1 | 一个文件同一时刻只能有一个写者；节点声明 `写范围`，跨节点重叠即视为冲突 | 并行写同一文件必丢改动 |
| R2 | 只有 Lead 能跑 `pnpm build`、`pnpm start`、`next dev`（`.next/` 与端口 3000 是独占资源） | 多个 build 同时跑会互相擦 `.next` |
| R3 | 子 agent 只准跑 `pnpm typecheck` 与 `pnpm vitest run <自己目录>`；不跑 e2e、不跑 build | 避免端口/产物争抢 |
| R4 | 子 agent 必须先 `read` 自己范围内已存在的文件再改（不许凭记忆重写） | 防止覆盖 Lead 的骨架 |
| R5 | 任何节点不许改 `package.json`（依赖已定）；确需加依赖先报 Lead | 安装是串行瓶颈 |
| R6 | 完成判据 = 命令 + 真实输出，不写「应该通过」 | 仓库 AGENTS §3「证据先于结论」 |
| R7 | 每个节点交付后，Lead 汇入总验收；不允许节点自己宣布整体完成 | 防止局部通过被当成全局通过 |

---

## 2. 节点表

| 节点 | 内容 | 写范围（互不重叠） | 依赖 | 验收命令 |
| --- | --- | --- | --- | --- |
| **N0** 工程底座 | `package.json`/`next.config.ts`/`tsconfig`/`postcss`/`vitest`/`playwright`/`globals.css`/令牌/`lib/utils.ts` + `pnpm install` | `web/{package.json,next.config.ts,tsconfig.json,postcss.config.mjs,vitest.config.ts,vitest.setup.ts,playwright.config.ts,components.json,.gitignore}`, `web/app/globals.css`, `web/lib/utils.ts` | — | `pnpm install` 成功；`pnpm typecheck` 0 错 |
| **N1** 后端 filename（唯一后端改动，D3） | 文档列表/详情加只读 `filename`；从 `document_versions.object_key` 末段解析；补后端用例 + OpenAPI 快照 | `src/pyknora/schemas/document.py`, `src/pyknora/db/repos/reads.py`, `src/pyknora/api/v1/documents.py`, `tests/api/test_api_documents.py`, `tests/unit/test_document_filename.py`, `web/contracts/openapi.json` | — | `uv run pytest -q tests/unit tests/api -k "document or filename"`；`uv run ruff check src tests` |
| **N2** 契约层 | OpenAPI 生成类型、错误信封 Zod、SSE 事件 Zod、分页适配器、MSW 桩与 fixtures | `web/lib/contracts/**`, `web/contracts/**`, `web/tests/msw/**` | N0 | `pnpm gen:contracts && pnpm typecheck` |
| **N3** 基础组件库 | Button/Field/Input/Textarea/Select/Dialog/Tabs/Table/Badge/StatusBadge/Skeleton/Toast/EmptyState | `web/components/ui/**` | N0 | `pnpm typecheck`；组件用例在 N9 覆盖 |
| **N4** 应用壳与设计系统落地 | 根 layout、Providers（QueryClient + Auth + Workspace）、`(app)` 侧栏壳、导航、PermissionGate、DisabledReason、`prefers-reduced-motion` 检查 | `web/app/layout.tsx`, `web/app/page.tsx`, `web/app/(app)/layout.tsx`, `web/components/app-shell/**`, `web/components/auth-guard.tsx`, `web/styles/**` | N0, N2, N3 | `pnpm typecheck`；三档宽度无横向滚动（e2e 在 N10） |
| **N5** 认证与权限 | 登录/注册页、`lib/auth`（令牌存取、权限工具）、`usePermissions`、identity 验活、401 全局清理 | `web/features/auth/**`, `web/app/(auth)/**`, `web/lib/auth/**` | N2, N3 | `pnpm typecheck`；`pnpm vitest run tests/unit/features/auth` |
| **N6** 知识库 | 列表/创建/改名/删除、游标分页、If-Match 乐观锁、410 重载 | `web/features/kbs/**`, `web/app/(app)/kbs/page.tsx`, `web/app/(app)/kbs/[kbId]/layout.tsx` | N2, N3, N4 | `pnpm typecheck`；`pnpm vitest run tests/unit/features/kbs` |
| **N7** 文档与作业 | 上传面板、文档表、切片列表、血缘时间线、JobBadge（**六态**）、JobPoller、取消、删除 | `web/features/documents/**`, `web/features/jobs/**`, `web/app/(app)/kbs/[kbId]/documents/page.tsx`, `web/app/(app)/kbs/[kbId]/documents/[documentId]/page.tsx` | N2, N3, N4 | `pnpm typecheck`；`pnpm vitest run tests/unit/features/documents tests/unit/features/jobs` |
| **N8** 检索调试 | QueryComposer、RetrievalTable（rrf/vector_rank/keyword_rank）、DegradedBanner、策略徽标 | `web/features/query/**`, `web/app/(app)/kbs/[kbId]/query/page.tsx` | N2, N3, N4 | `pnpm typecheck`；`pnpm vitest run tests/unit/features/query` |
| **N9** 流式问答 | 聊天状态机（retrieval→token*→done）、StreamingAnswer、CitationRail、AnswerStatus（预算中止/拒答/错误/中止/断流） | `web/features/chat/**`, `web/app/(app)/kbs/[kbId]/chat/page.tsx` | N2, N3, N4 | `pnpm typecheck`；`pnpm vitest run tests/unit/features/chat` |
| **N10** 成员管理 | 成员列表（UUID+角色）、添加成员（**按 principal_id，无邀请制**）、角色调整、移除、按钮权限 | `web/features/members/**`, `web/app/(app)/settings/members/page.tsx` | N2, N3, N4 | `pnpm typecheck`；`pnpm vitest run tests/unit/features/members` |
| **N11** 测试骨架 | 单测/契约测/MSW handlers 汇总、e2e 主链路、三档截图、axe 检查 | `web/tests/**`（含 `e2e/`） | N2–N10 | `pnpm test`；`pnpm exec playwright test`（Lead 执行） |
| **N12** 交付文档 | `web/README.md` 启动与验证说明、证据归档（构建输出、测试输出、截图路径） | `web/README.md`, `docs/plans/前端验收证据.md` | N11 | 人工核对八项总验收（计划 §9） |

---

## 3. 波次（并行组）

```text
W0  ── N0(串行根) ─┬─> W1
                   └─ N1(后端 filename，与 N0 并行，写 Python 侧不碰 web/)

W1  ── [N2 契约层] [N3 组件库]           (2 路，均只依赖 N0)
W2  ── [N4 应用壳]                       (1 路，依赖 N2+N3 → 单个节点允许串行)
W3  ── [N5 认证] [N6 KB] [N7 文档+作业] [N8 检索] [N9 聊天] [N10 成员]   (6 路并行)
W4  ── [N11 测试骨架]                    (1 路，汇总)
W5  ── [N12 交付文档]                    (1 路)
```

并行度上限：**6**（W3）；关键路径：`N0 → N2/N3 → N4 → N5..N10(取最慢) → N11 → N12`。

---

## 4. 各波次进入条件（Lead 逐条核对，不满足不开工）

| 波次 | 进入条件 |
| --- | --- |
| W0 | `web/` 骨架文件已落盘；`pnpm install` 退出码 0 |
| W1 | `pnpm typecheck` 在空骨架上 0 错 |
| W2 | N2 的 `lib/contracts/index.ts` 与 N3 的 `components/ui/index.ts` 均已存在且导出齐全 |
| W3 | N4 的 `Providers`、`PermissionGate`、`AppShell` 已可 import |
| W4 | W3 六路全部交付且 `pnpm typecheck` 0 错 |
| W5 | `pnpm test` 与 `playwright test` 退出码 0（Lead 执行） |

---

## 5. 施工口径（写进每个子 agent 的任务书）

### 5.1 已知的三个计划与实现冲突（施工时按此口径，不按计划原文）

1. **作业是六态不是五态**：`pending/processing/succeeded/failed/cancelled/superseded`；且后端 `TERMINAL_STATUSES` 漏了 `superseded`（`src/pyknora/services/queue/jobs.py:31`）→ 前端轮询停止条件必须是 `terminal === true || status === "superseded"`。
2. **成员管理没有邀请制**：`POST /workspaces/{ws_id}/members` 只收 `{user_id, role}`，列表只回 `{membership_id, workspace_id, user_id, role}`（刻意不回用户名）。所以 UI 文案用「添加成员（按 principal_id）」，不写"邀请"、不显示人名。
3. **`filename` 只从对象键解析**：`objects/{workspace_id}/{content_hash}/{filename}` 的最后一段；`None` 时回落显示短 `document_id`。

### 5.2 接口底座约定（N2/N3 必须导出这些名字，W3 依赖它们）

```ts
// lib/api/client.ts
export class ApiError extends Error { code: string; status: number; details?: unknown; requestId?: string }
export function apiRequest<T>(path: string, init?: ApiRequestInit): Promise<T>
export type ApiRequestInit = RequestInit & { workspaceId?: string; idempotencyKey?: string; token?: string | null }

// lib/contracts/index.ts
export const ErrorEnvelope, ChatSseEvent, JobStatus, AnswerVerdict, DoneStatus  // Zod
export type JobDetail, DocumentSummary, ChunkPage, QueryResponse, ChatRequest…

// components/ui/index.ts
export { Button, Input, Textarea, Label, Select, Dialog, DialogTrigger, DialogContent,
         Tabs, TabsList, TabsTrigger, TabsContent, Table, TableHeader, TableBody,
         TableRow, TableHead, TableCell, Badge, StatusBadge, Skeleton, Toast,
         EmptyState, Card, CardHeader, CardTitle, CardContent }
```

### 5.3 禁止事项

- 不引新依赖（`package.json` 已定）；不加 GSAP/Framer Motion/暗色主题/i18n（计划 §1 排除项）。
- 不用 `any`；不加 `@ts-ignore`；不用 `eslint-disable` 掩盖类型错误。
- 不写占位实现（`// TODO`、空函数体、假数据）；每条分支都要有真实错误处理。
- 不改 `src/pyknora/`（唯一例外 N1 自己）。

---

## 6. 与总验收（计划 §9）的映射

| 总验收项 | 由哪个节点提供证据 |
| --- | --- |
| 生产构建通过 | Lead 执行 `pnpm build`（W4 后） |
| 前端自动化全部通过 | N11：`pnpm test` 输出 |
| 后端相关测试通过 | N1：pytest 输出 |
| 真实主链路可走通 | N11 e2e：注册→建库→上传→检索→问答→删除 |
| 异常分支不伪装成功 | N9/N7 单测 + e2e（error 后仍收 done、坏文件 PARSE_FAILED、deduped 分支） |
| 三档宽度无横向溢出 | N11 e2e 在 1440/1024/390 断言 `scrollWidth <= clientWidth` |
| 键盘与对比度 | N11 axe（含 keyboard 走查脚本） |
| 设计检查无未解释失败 | N12 证据归档 |

---

## 7. 执行结果（2026-11-01 实际执行，Lead 记录）

> 口径：本节只写**跑过的命令与真实输出**。没跑的写「未跑」，不写「预期通过」。数字均为 Lead 复跑所得（2026-11-01 22:0x–22:1x）。

### 7.1 逐节点状态

| 节点 | 状态 | 真实证据 |
| --- | --- | --- |
| **N0** 工程底座 | ✅ 完成 | `pnpm install` 成功；`pnpm typecheck` → `TYPECHECK_EXIT=0`；`web/` 实测 **174 个文件**（`/KB`，不含 `node_modules`/`.next`） |
| **N1** 后端 filename | ✅ 完成 | 真因：上传只写 `desired_document_version_id`（202），`active` 要等 Worker 发布，原 join 只看 `active` → 刚上传的 `filename` 恒 `null`。改 `src/pyknora/db/repos/reads.py::_version_key_join()` 为 `coalesce(desired, active)`（两指针都空才回 `None`）。`uv run pytest -q tests/api/test_api_documents.py` → **41 passed**；`ruff check src tests` → All checks passed；`ruff format --check` → 133 files unchanged。`web/contracts/openapi.json`（110 KB，2026-11-01 20:30）已重新生成，含 `filename` 字段 |
| **N2** 契约层 | ✅ 完成 | `lib/api/client.ts`(317)/`hooks.ts`(69)/`lib/contracts/index.ts`(574)/`sse.ts`(224)/`openapi.d.ts`(2298 生成)；`tests/contract/` 6 文件 **95 用例全绿** |
| **N3** 组件库 | ✅ 完成 | `components/ui/` **17 文件** + `tests/unit/components/` 7 文件 **38 用例全绿** |
| **N4** 应用壳 | ✅ 完成 | `app/layout.tsx`、`app/(app)/layout.tsx`、`components/app-shell/` **10 文件**；`pnpm build` 出 8 条路由即为其接线证据 |
| **N5** 认证与权限 | ✅ 完成 | `lib/auth/` 7 文件 + `features/auth/` 7 文件 + `app/(auth)/login/page.tsx`；`tests/unit/features/auth/` 7 文件 **32 用例** |
| **N6** 知识库 | ✅ 完成 | `features/kbs/` 6 文件 + `/kbs` 页 + `[kbId]/layout.tsx`（导出 `useKbId()`）；**9 用例** |
| **N7** 文档与作业 | ✅ 完成 | `features/documents/` 9 + `features/jobs/` 7；`tests/unit/features/{documents,jobs}/` 4 文件 **8 用例** |
| **N8** 检索调试 | ✅ 完成 | `features/query/` 9 文件 + `/query` 页；`tests/unit/features/query/` 3 文件 **15 用例** |
| **N9** 流式问答 | ✅ 完成 | `features/chat/` 14 文件 + `/chat` 页；`tests/unit/features/chat/` 2 文件 **15 用例** |
| **N10** 成员管理 | ✅ 完成（2026-11-02 补交付） | `web/features/members/` **10 文件**（api/queryKeys/roles/errors/hooks + RoleSelect/MemberAddDialog/MemberTable/OwnerTransferDialog）+ `app/(app)/settings/members/page.tsx` + 5 个测试文件 **36 用例**。逐行核对后端后纠正 5 处：改角色是 **PATCH** 非 PUT、转移体键是 **`owner_principal_id`**、`/permissions` 只自省（角色目录在 `/roles`，键 `role_key`）、改角色/删成员越权回 **404 非 403**、列表只有 `user_id`（无姓名/邮箱） |
| **N11** 测试骨架 | ✅ 完成（2026-11-02 补交付） | 单测 + 契约测 **32 文件 / 250 用例全绿**；e2e **6 个 spec + 12 例全过**（`pnpm exec playwright test` → `12 passed (1.0m)`，EXIT=0）；axe 扫 5 个路由 **全 0 违规**（`A11Y_EXIT=0`）。详见 §7.4 |
| **N12** 交付文档 | ✅ 完成（Lead 手写，2026-11-01） | `web/README.md` 落盘：快速开始（后端 8010 → 前端代理）、目录结构、脚本表、**8 条契约口径**（六态 `superseded`、`ChunkPage.total` 是页内条数、`MemberPage` 键 `members`、`filename` 取自 `coalesce(desired, active)`、lineage 无发布事件、`invalid_citations` 是整数、SSE 顺序与错误降级、`If-Match` 裸数字/428）、测试与 e2e 前置、**已知缺口诚实表** |

### 7.2 三绿复验（Lead 亲自执行，2026-11-01 22:1x）

```text
pnpm typecheck        → tsc --noEmit 无诊断输出；TYPECHECK_EXIT=0
pnpm exec vitest run  → Test Files 28 passed (28) / Tests 212 passed (212)
                        Duration 84.65s；VITEST_EXIT=0
pnpm build            → BUILD_EXIT=0
Route (app)
┌ ○ /
├ ○ /_not-found
├ ○ /kbs
├ ƒ /kbs/[kbId]/chat
├ ƒ /kbs/[kbId]/documents
├ ƒ /kbs/[kbId]/documents/[documentId]
├ ƒ /kbs/[kbId]/query
└ ○ /login
```

原始日志：`.tmp_ref/web-verify.log`（未入库，可重跑再生成）。

### 7.3 与计划的偏差（施工时按 §5.1 口径落地，未按计划原文）

1. **N10 成员管理未交付**：不是"跳过"，是 W3 只有 6 路并发额度（`active child limit: 8`，同时段还要留给 N1/N2），派单时 N10 第一次因超限没派出去，后续未补派。前端因此**没有成员管理页**——`/kbs` 侧栏无该入口，`features/members` 无代码。
2. **e2e 与 axe 未跑**：见 §7.1 N11。`AbortSignal`/`FormData` 在 jsdom 与 undici 之间跨 realm，`signal` 透传与 multipart `Content-Type` **只能靠真浏览器验**，MSW 层已按"不透传"绕开并在代码里注明——这条只能由 e2e 收口。
3. **计划里"作业五态"实际是六态**：前端 `JOB_STATUSES` 收窄为六态，轮询停止条件写 `terminal === true || status === "superseded"`（后端 `TERMINAL_STATUSES` 确实漏了 `superseded`）。
4. **计划里"成员邀请制"不存在**：`POST /workspaces/{ws_id}/members` 只收 `{user_id, role}`，列表不回用户名 → UI 文案必须写「按 principal_id 添加」。
5. **`ChunkPage.total` 是页内条数**、`MemberPage` 键是 `members` 且无分页、`lineage` 只有版本链**没有发布事件字段**——均已按后端实况实现，未按计划想象。

### 7.4 收尾轮（2026-11-02，补 N10/N11 + 三个真 bug）

这一轮把 §7.3 的第 1、2 条缺口补齐，并在真浏览器 + 真后端下抓出三个单测看不见的缺陷。

**补交付**

- **N10 成员管理**：36 用例（api 8 / errors 13 / hooks 6 / 组件 9）。导航入口无需新增——`components/app-shell/nav-items.ts:23-28` 已指向 `/settings/members`。
- **N11 e2e**：`playwright.config.ts` 重写为 3 条 `webServer`（Next 3000 + uvicorn 8000 + 假 OpenAI SSE 8123）+ `globalSetup/Teardown`；6 个 spec 覆盖 登录→跳转、建库、上传 202→轮询 succeeded、检索排名表、问答流式收敛、5 路由 axe。**真实后端，不是 mock**——要验的正是 `/api/*` rewrites、SSE 过 Next 代理是否被缓冲、multipart 字段名。

**三个真 bug（都是 MSW 单测测不出来的）**

| # | 缺陷 | 证据 | 修法 |
| --- | --- | --- | --- |
| P0-1 | 业务请求**不带 `Authorization`**：`client.ts` 只在显式传 `token` 时加头，而 `features/{kbs,documents}/api.ts` 一个都没传 → 真浏览器里列表永远空白、显示「登录状态已失效」 | e2e trace：`/auth/me` 带 authorization 200，`/knowledge-bases` 不带 → 401。MSW 不校验鉴权，所以 212 用例全绿也发现不了 | `lib/api/client.ts` 改三档语义：**省略**=用 `lib/auth/token-store` 的会话令牌；**显式 `null`**=强制匿名（登录/注册）；**字符串**=覆盖。不能用 `??` 合并，否则「退出登录后仍带旧令牌」 |
| P0-2 | 对比度不达 AA：`--color-fg-subtle: #7d8f95` 在白底仅 **3.36:1**、canvas 底 **3.13:1**，而它被用在 10–12px 正文（11 处） | axe `color-contrast` 把 5 个路由全判 serious | `app/globals.css` 压暗到 `#5f7175` → **5.12:1 / 4.76:1**，仍比 `fg-muted(#566a70)` 浅，层级不变 |
| P1-2 | 无障碍名与可见文字不符：改名按钮可见「改名」但 `aria-label="重命名「X」"` | axe `label-content-name-mismatch` serious×2（仅 `/kbs`） | `features/kbs/KbRowActions.tsx` 改 `aria-label={\`改名「${kb.name}」\`}`，让可见词出现在无障碍名开头 |

**本轮真实输出（Lead 亲自执行，2026-11-02 09:1x–09:2x）**

```text
pnpm typecheck                    → TYPECHECK_EXIT=0
pnpm exec vitest run              → Test Files 32 passed (32) / Tests 250 passed (250)   VITEST_EXIT=0
pnpm build                        → BUILD_EXIT=0，9 条路由（新增 /settings/members）
pnpm exec playwright test         → 12 passed (1.0m)                                    E2E_FULL_EXIT=0
pnpm exec playwright test tests/e2e/a11y.spec.ts
                                  → 5 passed (43.9s)                                    A11Y_EXIT=0
                                   [axe] 5 条路由均 critical=0 serious=0 moderate=0 minor=0
```

**未验证项（诚实清单，未做不写"通过"）**

1. 移动端窄屏（原 `mobile` 项目已删）：底部 Tab 布局无自动化覆盖。
2. 聊天页 axe 只扫"提问前空态"——流式期间 DOM 每 60ms 变一次，不可复现；"回答已渲染"状态的无障碍未验。
3. 只扫 5 个路由的默认态；错误态、空态、对话框展开态、文档详情页未扫。
4. 键盘可达性与读屏器实测未做；深色模式未做（设计上无深色主题）。
5. CI 里没有 `playwright install` 步骤（本地靠 `pnpm exec playwright install chromium`，build 1248）。
6. **P1-1 后端未修**：建库回 201 后立刻上传偶发 `404 knowledge base not found`（约 8 次 1 次，库里该行存在）。推断是 `api/deps.py` 的 `get_session` yield 依赖在响应发出后才 commit，形成写后读窗口；e2e 侧退避重试 3 次并打 `[e2e][warn]` 留痕，**没有掩盖**。
7. `web/lib/api/client.ts` 的会话令牌兜底只在 e2e 与契约测里验过；真实多标签页登出（另一标签 `storage` 事件清令牌）未验。

