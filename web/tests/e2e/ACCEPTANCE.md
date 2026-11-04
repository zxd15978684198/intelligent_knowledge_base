# PyKnora 前端端到端验收报告（task-1 / N10）

- 验收对象：`E:\projects\pyknora\web`（Next.js 16.4.3 + React 19.3.1）
- 验收方式：Playwright 真实浏览器 + **真实后端**（FastAPI + docker pgvector + worker）
- 运行命令：`cd E:\projects\pyknora\web && pnpm exec playwright test`
- **结果：EXIT=1，12 例，7 passed / 5 failed，118 秒**

## 0. 结论

6 项必测里 **5 项通过**（登录、知识库、上传边轮询、检索、问答流式），第 6 项 **axe 无障碍未通过**：
5 个路由各 1 条 `serious` 的 `color-contrast`，`critical` 全为 0。

唯一挡住验收的产品缺陷是设计 token 的对比度（`--color-fg-subtle: #7d8f95` 落在 11–12px 文本上，
3.36:1 与 3.13:1，AA 要 4.5:1）。它是**一处 token 改动**，但改 `web/app/globals.css` 不在我的写范围，
按要求只记录、不修。

另有两条本次运行期间暴露的问题：一条后端写后读竞态（P1-1，未修，测试侧退避留痕），
一条前端鉴权缺失（P0-1，验收期间已被他人修复，本轮已复验通过）。

## 1. 改了哪些文件（全部在写范围内）

**修改**

- `web/playwright.config.ts` —— 重写。原文件只有 1 条 webServer、desktop+mobile 双项目。

**新增**

| 文件 | 作用 |
| --- | --- |
| `web/tests/e2e/auth.spec.ts` | 用例 1：登录成功跳 `/kbs`；错密码停 `/login` |
| `web/tests/e2e/kbs.spec.ts` | 用例 2：列表渲染 + 新建 KB 对话框提交 |
| `web/tests/e2e/documents.spec.ts` | 用例 3：上传 → 202 → 轮询到 succeeded |
| `web/tests/e2e/query.spec.ts` | 用例 4：输入查询 → 排名表出现 |
| `web/tests/e2e/chat.spec.ts` | 用例 5：发问 → 流式回答收敛到 done |
| `web/tests/e2e/a11y.spec.ts` | 用例 6：axe 扫 5 个路由 |
| `web/tests/e2e/fixtures/power-module.txt` | 上传用的固定文档（内容能稳定触发 SUPPORTED 判定） |
| `web/tests/e2e/support/paths.ts` | `WEB_ROOT` / `REPO_ROOT` 定位（不依赖 `FullConfig.rootDir`） |
| `web/tests/e2e/support/env.ts` | 基址、账号密码、超时等常量 |
| `web/tests/e2e/support/state.ts` | globalSetup 建的账号在 worker 之间传递 |
| `web/tests/e2e/support/api.ts` | 直连 8000 的布置/取数封装 |
| `web/tests/e2e/support/backend-env.ts` | 后端起进程用的环境变量（单一事实源） |
| `web/tests/e2e/support/worker.ts` | 起/停 `scripts/run_worker.py` |
| `web/tests/e2e/support/global-setup.ts` | 探活三个服务、起 worker、注册测试账号 |
| `web/tests/e2e/support/global-teardown.ts` | 停 worker、清理状态文件 |
| `web/tests/e2e/support/seed.ts` | `seedEmptyKb` / `seedKbWithDoc` |
| `web/tests/e2e/support/ui.ts` | `loginThroughUi(page)` |
| `web/tests/e2e/support/fake-chat-upstream.mjs` | 假 OpenAI SSE 服务（127.0.0.1:8123） |

**没改**：`web/features/**`、`web/components/**`、`web/app/**`、`web/lib/**`、`web/next.config.ts`、`web/package.json`。
**没有新增依赖**：`@playwright/test`（解析到 1.64.1）与 `@axe-core/playwright`（4.14.0）本来就在 devDependencies 里。
唯一的机器级安装是浏览器二进制：`pnpm exec playwright install chromium`（build 1248，仓库外）。
**没有 `git commit`。**

## 2. 命令与真实输出

### 2.1 类型检查（我写的 spec 也在 `web/tsconfig.json` 的 `**/*.ts` 里）

```text
$ cd E:\projects\pyknora\web && pnpm typecheck
$ tsc --noEmit
EXIT=0
```

### 2.2 浏览器二进制

```text
$ pnpm exec playwright install chromium
...  292 秒
EXIT=0
```

### 2.3 端到端

```text
$ cd E:\projects\pyknora\web && pnpm exec playwright test
EXIT=1
SECONDS=118

  x   1 [desktop] › tests\e2e\a11y.spec.ts:137:7 › 无障碍扫描 › /login (5.7s)
  x   2 [desktop] › tests\e2e\a11y.spec.ts:143:7 › 无障碍扫描 › /kbs (6.2s)
  x   3 [desktop] › tests\e2e\a11y.spec.ts:151:7 › 无障碍扫描 › /kbs/{kbId}/documents (4.0s)
  x   4 [desktop] › tests\e2e\a11y.spec.ts:162:7 › 无障碍扫描 › /kbs/{kbId}/query (2.6s)
  x   5 [desktop] › tests\e2e\a11y.spec.ts:175:7 › 无障碍扫描 › /kbs/{kbId}/chat (2.5s)
  ok  6 [desktop] › tests\e2e\auth.spec.ts:14:7 › 登录 › 真实账号登录后跳转 /kbs 并进入应用 (1.5s)
  ok  7 [desktop] › tests\e2e\auth.spec.ts:32:7 › 登录 › 密码错误时停在 /login 并给出 role=alert (925ms)
  ok  8 [desktop] › tests\e2e\chat.spec.ts:18:7 › 问答 › 发问后出现流式回答并收敛到已完成 (4.5s)
  ok  9 [desktop] › tests\e2e\documents.spec.ts:18:7 › 文档上传与索引 › 上传 txt → 202 → 轮询到 succeeded → 列表可检索 (3.5s)
  ok 10 [desktop] › tests\e2e\kbs.spec.ts:14:7 › 知识库 › 列表渲染出已有知识库与加载计数 (4.5s)
  ok 11 [desktop] › tests\e2e\kbs.spec.ts:26:7 › 知识库 › 新建知识库对话框提交后列表出现新库 (7.7s)
  ok 12 [desktop] › tests\e2e\query.spec.ts:16:7 › 检索调试 › 输入查询后出现排名表与命中摘要 (3.3s)

  5 failed
  7 passed (1.9m)
```

全局准备阶段（globalSetup）实测正常：uvicorn `/health` → 200（`embedder=test/hashing`）、
假上游 `127.0.0.1:8123` 起、`pnpm build && pnpm start` → 8 条路由、worker 起、测试账号注册成功。

### 2.4 运行历史（含失败轮，避免只报最好的一次）

| 轮次 | 结果 | 失败根因 |
| --- | --- | --- |
| 1 | EXIT=1，0 例执行 | `globalSetup` 用 `FullConfig.rootDir` 定位仓库根，而它是 testDir（`web/tests/e2e`）→ `spawn ...\web\tests\.venv\Scripts\python.exe ENOENT` |
| 2 | EXIT=1，12 例全失败 | 缺浏览器二进制（需 build 1248，本地只有 1228/1243） |
| 3 | EXIT=1，3 passed / 9 failed | 产品缺陷 P0-1：业务请求不带 `Authorization` → 401 |
| 4 | EXIT=1，2 passed / 10 failed | P0-1 仍在；另 1 例布置阶段撞上 P1-1（建库后立刻上传 404） |
| 5（最终） | **EXIT=1，7 passed / 5 failed** | 只剩 P0-2（对比度）；P0-1 已被他人修复，本轮复验通过 |

## 3. 用了 mocked 还是真实后端

**真实后端**，没有任何 `page.route()` 拦截。

理由：这轮要验收的恰恰是"接起来到底能不能用"，而 mocked 层看不见三样东西——
`next.config.ts` 的 `/api/*` rewrites 是否生效、SSE 经过 Next 代理会不会被缓冲、
multipart 字段名与后端是否对得上。事实也证明了这一点：P0-1（401）和 P1-1（竞态）
只有真打后端才暴露得出来，MSW 不校验鉴权、也不会晚提交。

接线方式（未改一行产品配置）：

- 浏览器只访问 `http://127.0.0.1:3000`（Next），同源 `/api/*` 由 `next.config.ts` 的 rewrites 转发；
- 转发目标用环境变量 `API_PROXY_TARGET=http://127.0.0.1:8000` 指定（config 里本来就支持）；
- Playwright 起三个进程：uvicorn（venv python 直起，避免 `child.kill()` 只杀 uv 留下孤儿）、
  假 OpenAI SSE 服务（回答内容固定，可稳定断言）、`pnpm build && pnpm start`；
- 测试自己的布置/断言直连 `127.0.0.1:8000`，不经过 UI。

为什么需要假的上游：`services/rag/answer.py:324-328` 在 `chat_api_key` 为空时直接抛
`DEPENDENCY_UNAVAILABLE`（503），聊天链路没有 key 就跑不起来；而真 key 会让断言依赖模型输出，
不可复现。假上游只负责按 OpenAI SSE 形状吐固定三帧，PyKnora 自身代码全部真实执行。

## 4. axe 结果表

规则集：`wcag2a`、`wcag2aa`、`wcag21a`、`wcag21aa`。

| 路由 | critical | serious | moderate | minor | 违规 id |
| --- | --- | --- | --- | --- | --- |
| `/login` | 0 | **1** | 0 | 0 | `color-contrast`（3 节点） |
| `/kbs` | 0 | **1** | 0 | 0 | `color-contrast`（2 节点） |
| `/kbs/{kbId}/documents` | 0 | **1** | 0 | 0 | `color-contrast`（2 节点） |
| `/kbs/{kbId}/query` | 0 | **1** | 0 | 0 | `color-contrast`（3 节点，含 3.13:1） |
| `/kbs/{kbId}/chat` | 0 | **1** | 0 | 0 | `color-contrast`（3 节点） |

全部 5 个路由 `critical=0`、`moderate=0`、`minor=0`；`serious` 各 1 条，均为同一 token 问题：

```text
[axe] /login critical=0 serious=1 moderate=0 minor=0
[axe]   serious color-contrast .tracking-\[0\.18em\] :: insufficient color contrast of 3.36
        (foreground #7d8f95, background #ffffff, 12px, normal). Expected 4.5:1
[axe]   serious color-contrast section > .leading-relaxed.text-fg-subtle :: 3.36 (同上)
[axe] /kbs/{kbId}/query
[axe]   serious color-contrast span[title="由服务端环境变量决定，切换需重启 API。"] > .font-mono
        :: insufficient color contrast of 3.13 (foreground #7d8f95, background #f5f7f8, 12px)
```

**这不是 axe 误报**：背景色都被算成了具体值（`#ffffff` / `#f5f7f8`），没有渐变或半透明。
按要求的判定标准（critical 与 serious 必须为 0），**这一项验收未通过**。

## 5. 业务 bug 清单

### P0-2（未修，阻塞验收）设计 token 对比度不足

- 现象：5 个路由都有 `serious` 级 `color-contrast`。
- 位置：`web/app/globals.css:20` `--color-fg-subtle: #7d8f95`，被大量 11–12px 小字使用
  （导航分组标题、页脚、面板说明、`/query` 页的环境提示）。
- 数字：`#7d8f95` on `#ffffff` = **3.36:1**；on `#f5f7f8`(`--color-canvas`) = **3.13:1**；
  AA 对常规字重小字要求 **4.5:1**。
- 复现：`cd web && pnpm exec playwright test tests/e2e/a11y.spec.ts`，看 stdout 的 `[axe]` 行。
- 影响面：只改 token（例如压到 `#5f7175` 一档）或把 `fg-subtle` 的字号提到 18.66px+/加粗即可；
  改 `globals.css` 不在我的写范围，未动。

### P0-1（验收期间已被他人修复，本轮复验通过）业务请求不带 Authorization

- 现象（第 3、4 轮）：`/kbs` 主区显示 `登录状态已失效，请重新登录。`，其余页面同样 401；
  侧栏 identity 却正常（说明 `/auth/me` 是通的）。
- 证据：trace 里 `GET /api/v1/auth/me` → 200 且带 `authorization`；
  `GET /api/v1/knowledge-bases?limit=20` → **401 且没有任何 authorization 头**，只有 `x-workspace-id`。
  documents / query / 建库的请求同样。
- 当时根因：`web/lib/api/client.ts:143` 只在显式传 `init.token` 时才加头，
  而 `web/features/kbs/api.ts` 的 `scopeInit()` 只拷 `workspaceId`/`signal`；
  `features/{documents,query,jobs}/api.ts` 全文没有 "token"。
- 为何单元测试测不出：vitest 走 MSW，MSW 不校验鉴权，缺头也 200。
- 现状：`web/lib/api/client.ts` 于 **2026-11-02 09:03:30** 被他人改动，改为默认从
  `lib/auth/token-store` 注入会话令牌（`client.ts:19` `import { getToken }`，
  `client.ts:153-155` `const token = init.token === undefined ? getToken() : init.token`）。
  最终这轮运行 401 只剩 1 条，且是 auth 用例里故意输错密码的那次 → 复验通过。
  **这个修复不是我做的**，记录在此仅为闭环。

### P1-1（后端，未修）建库后立刻上传偶发 404（写后读竞态）

- 现象：`POST /knowledge-bases` 已回 201，紧接着 `POST /knowledge-bases/{id}/documents`
  返回 `404 {"error":{"code":"RESOURCE_NOT_FOUND","message":"knowledge base not found"}}`，
  而库里该行确实存在。
- 发生频率：第 4 轮 8 次布置数据里 1 次。
- 复现：连续建库后立刻上传（`web/tests/e2e/support/api.ts` 的 `seedKbWithDocument` 天然就是这个模式），
  多跑几轮可撞到；撞到时能在数据库里查到该 kb 行。
- 推断根因：`src/pyknora/api/deps.py:50` 的 `get_session` 是 yield 依赖，提交发生在
  `src/pyknora/db/session.py:53`（`session_scope.__aexit__`），而 FastAPI 的 yield 依赖清理排在
  响应发出之后 → 客户端拿到 201 时事务可能尚未提交。属"读到了自己还没提交的写"的经典窗口。
- 处理：布置数据不是被测对象，测试侧加了最多 3 次、退避 250/500ms 的重试，且每次重试都往 stdout
  打 `[e2e][warn] 上传 404：KB … 刚创建就找不到（疑似 P1-1 提交晚于响应），第 N 次重试`——
  留痕不掩盖。真实用户感知不到（人不会在毫秒级连点），但脚本化调用会遇到。
- 未改产品代码。

## 6. 没做什么 / 未验证项

1. **没修任何产品代码**，包括 P0-2 那个 token——按纪律只记录，等 Lead 决定。
2. **移动端未验证**：原 config 里的 `mobile`（Pixel 7 390x844）项目被我删除。理由：本轮 6 项
   验收都在桌面语义下，双项目会让用例数与时长相乘却不增加证据强度；窄屏的 Tabs 布局
   （`ChatScreen` 的 `useWideLayout`）没有覆盖。
3. **聊天页的 axe 扫描停在提问前空态**，没扫流式过程中的 DOM：流式期间 60ms 变一次，
   扫描结果不可复现。问答页在"回答已渲染"状态下的无障碍**未验证**。
4. **只扫了 5 个路由的默认态**，没扫错误态、空态、对话框打开态、`/kbs/{kbId}/documents/{documentId}`。
5. **没做键盘可达性/焦点顺序/读屏器实测**，只跑了 axe 的静态规则。
6. **深色模式未验证**（若产品支持）。
7. P1-1 只在测试侧退避，后端竞态本身**未验证是否已存在于更早的版本**、也未做压测复现。
8. 浏览器二进制是本机安装（`chromium-1248`），CI 上需要单独的 `playwright install` 步骤——
   本轮未配置 CI。
9. `@playwright/test` 在 `package.json` 里写 `^1.56.1`，实际解析到 **1.64.1**；浏览器 build 号与
   这个实际版本绑定，换版本需要重新 `install`。
