# `web/contracts/` —— 前端与后端的契约边界

这个目录只放**后端给的东西**，不放前端类型（前端类型在 `web/lib/contracts/`）。

| 文件 | 谁产生 | 能不能手改 |
| --- | --- | --- |
| `openapi.json` | N1 节点从 FastAPI 导出（`create_app().openapi()`） | **不能**。它是后端的镜像，改了就跟后端不一致了 |
| `lib/contracts/openapi.d.ts` | `pnpm gen:contracts`（openapi-typescript） | **不能**。每次重新导出 `openapi.json` 后重新生成 |

## 更新流程

```bash
# 后端改了 schema / 路由之后
pnpm gen:contracts     # openapi.json → lib/contracts/openapi.d.ts
pnpm typecheck         # tests/contract/schema-drift.test.ts 的类型级断言会告诉你手写类型哪里跟不上了
pnpm test
```

`pnpm gen:contracts` 只做一件事：把 `openapi.json` 编译成 `openapi.d.ts`。它**不会**校验后端是否真的按这个 schema 返回——那是 `tests/api`（后端）的事。

## 生成与手写的分界

`openapi.json` 只覆盖"有 `response_model` 的 REST 端点"。有两块**必然缺席**，只能手写，而且因为它们要参与运行时分支判断，所以用 Zod 而不是纯类型：

| 契约 | 为什么不在 openapi 里 | 手写位置 |
| --- | --- | --- |
| 错误信封 | `main.py` 的 `@app.middleware("http")` 与异常处理器现拼的 JSON | `lib/contracts/index.ts` 的 `errorEnvelopeSchema` |
| SSE（`/chat`） | `StreamingResponse` 没有 `response_model`，生成器函数不会被推断进 schema | `lib/contracts/index.ts` 的 `chatSseEventSchema` + `lib/contracts/sse.ts` |
| `/health` | 返回 `dict[str, Any]`，没有响应模型 | `healthResponseSchema` |

其余实体（`KnowledgeBase` / `DocumentSummary` / `JobDetail` …）是**手写 interface + 类型级漂移断言**，不是直接引用 `components["schemas"][...]`：

- 生成类型里塞满了 pydantic 的 `description`（有的字段描述长达三段），直接引用会让业务组件到处是噪音；
- 手写能写"为什么"，比如 `MemberPage.members` 为什么不是 `items`、`ChunkPage.total` 为什么不能拿来算总页数；
- 漂移由 `tests/contract/schema-drift.test.ts` 兜住：断言方向是 **`后端形状 extends 前端类型`**。后端删字段/改名/把可选改必填 → `tsc` 报错；后端**加**字段 → 断言仍成立（这是刻意的，不做双向，否则每次后端加字段前端都要跟着改）。

## 三处刻意的偏差（都在 drift 测试里单独拆开断言）

1. **`JobDetail.status` 收窄成六态**。后端声明是裸 `str`（`api/v1/jobs.py`），openapi 里是 `string`；前端要按状态穷举渲染，所以收成 `JobStatus`。风险由 `jobStatusSchema` 在运行时兜。
2. **`ChunkPage.items` 手写成 `ChunkItem[]`**。后端这个端点的 `response_model` 是 `list[dict[str, Any]]`（没用 `ActiveChunk` 模型），openapi 里只能是"任意对象数组"；前端要读字段只能手写形状。这条无法做类型级比对，改后端时靠集成测试发现。
3. **`MembershipDetail.role` 保持 `string`**。角色是 `roles` 表里的一行，管理端可以新建，写成 union 会在管理员加角色时静默失配。

另外 `QueryResponse.retrieval_strategy`、`MembershipDetail.role` 在后端都是裸 `str`，前端也保持 `string`——不要因为"看起来应该是枚举"就收窄。

## 三个坑（类型层看不出来的）

### 1. 分页有三种，语义各不相同

| 端点 | 分页 | `total` 的含义 |
| --- | --- | --- |
| 知识库列表 | 游标（`next_cursor`） | 没有 total |
| 文档列表 | `limit` / `offset` | **全量条数**（能算总页数） |
| 文档切片列表 | `limit` / `offset` | **页内条数**（不能算总页数） |

饶口令一样，但照着字段名"想当然"用会得到永远 1 页的切片列表。

### 2. 作业是六态，但后端的 `terminal` 只认五态

`pending` / `processing` / `succeeded` / `failed` / `cancelled` / `superseded`。
`src/pyknora/services/queue/jobs.py:31` 的 `TERMINAL_STATUSES` **漏了 `superseded`**，所以被新构建取代的作业会返回 `status="superseded"` + `terminal=false`。停止轮询必须用：

```ts
isJobTerminal(job)   // job.terminal || job.status === "superseded"
```

只看 `terminal` 会永远轮询一个已经结束的作业。

### 3. SSE：`error` 之后还有一帧 `done`

顺序是 `retrieval` → `token`* → `done`；出错时先发 `error`，**然后照常补一帧 `done`**（`api/v1/chat.py:293-294`，因为 `StreamingResponse` 已经开始发送，只能用事件收尾）。所以消费方不能"收到 error 就关流"，必须读到 `done` 才能把"生成中"改成终态。

帧格式（后端一个 `_sse()` 拼的，没有换行以外的转义）：

```
event: token
data: {"text":"先"}

```

## 错误信封

```json
{"error":{"code":"FORBIDDEN","message":"…","retryable":false,"details":null},"request_id":"…"}
```

- 401/403/404/409/410/422/428/500 全部共用这一形状（`AppError` / `RequestValidationError` / `StarletteHTTPException` / 兜底 `Exception` 四条路径）。
- 每个响应都带 `X-Request-ID` 头（`main.py` 的中间件），`ApiError.requestId` 优先取它。
- 422 的 `details` 是 `{"errors":[{loc,type,msg}]}`，**不是** pydantic 默认的 `{"detail":[...]}`。
- 反代 502 之类返回 HTML 时不会是信封，`apiRequest` 会降级成 `code="INVALID_RESPONSE"` 并保留状态码——错误处理代码不该在错误时再崩一次。
