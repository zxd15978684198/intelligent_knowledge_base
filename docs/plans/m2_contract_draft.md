# 成员2 契约草案：摄取与索引交给下游的三件共享物

> 状态：草案 v2（2026-10-29）｜需要成员3、成员5 各回一句「同意」或「改 X」
> 读者：成员3（检索与 RAG）、成员5（来源追踪与索引迁移）
> 依据：本文件所有列名与签名**逐条对照过 `src/pyknora/` 现有代码与 `migrations/`**，不是从设计文档抄的。凡与旧计划书不一致处，见 §7。
> v2 变更（2026-10-29，仅 §2 / §3.5 / §5 / §7 受影响）：中文 FTS 已修好——`tsvector` 改为 `to_tsvector('simple', pyknora_cjk_bigram(content))`，查询侧改用 `pyknora_cjk_tsquery()`；**新增 `ChunkRepo.search_keyword` 原语**（原先让成员3 来找我定 `search_text` 的那句作废）。迁移见 `migrations/0007_cjk_bigram_fts.sql`。

---

## §1 为什么要冻结

这节解决：成员3 与成员5 的「为什么今天就得回话」——不回答，你们下周会替我的字段名返工。

三句话说完：

1. 一个字段名一旦被写进别人的代码，改它就等于「改我这边 + 改你那边 + 一次数据库迁移（`migration`）」三方联动，成本从五分钟变成半天。
2. 所以**先冻结读契约**（`read contract`，即你们从我这里取数的口子）：列清单、Protocol 签名、返回模型——这三样今天就能定，因为我方读路径（`src/pyknora/db/repos/reads.py`）已经写完了。
3. **写契约**（`write contract`，即你们往我的队列里塞作业）留到实现稳定后再冻，那时才有真实调用点能验证签名；现在冻写契约等于冻一个没跑过的形状。

| 时点 | 冻什么 | 现在能不能改 | 改一次的代价 |
| --- | --- | --- | --- |
| **今天**（本文） | `chunks` 列清单、`v_active_chunks` 视图列、`C2` 四个 Protocol、`EmbeddingProfile` 字段名 | 能，改一行字 | 改列名 = 半天；加列 = 5 分钟 |
| 实现稳定后 | `C4` 入队 / `C6` 发布 的最终签名 | 暂时按 §6 的形态写 | 改签名只动一个文件（Protocol 只有一处定义） |

---

## §2 共享物之一：`chunks` 表的完整列清单

这节解决：成员3 要写检索 SQL、成员5 要落 `citations`，你们得知道到底有哪几列、哪几列能依赖。

「契约列」（`contract column`）= 你们可以写进代码、我不会单方面改；「内部列」（`internal column`）= 成员2 的实现细节，我改它不需要通知任何人（但它也不在视图里，你们本来也读不到）。

| 列名 | 类型 | 可空 | 含义 | 谁会读它 | 归类 |
| --- | --- | --- | --- | --- | --- |
| `chunk_id` | uuid | 否（主键） | 切片唯一标识 | 成员3 返回引用、成员5 落 `citations` | 契约 |
| `workspace_id` | uuid | 否 | 所属工作区，`Scope` 的 SQL 谓词打在它上面 | 成员3 过滤、成员5 过滤 | 契约 |
| `document_id` | uuid | 否 | 所属文档 | 成员3 按文档取数、成员5 反查 lineage | 契约 |
| `build_id` | uuid | 否 | 这条切片属于哪次索引构建（`index build`） | 成员5 迁库时按 build 映射旧引用 | 契约 |
| `index_version` | int | 否 | 同一文档的第几次构建，单调不减 | 成员3 调试、成员5 对账 | 契约 |
| `attempt_id` | uuid | 否 | 哪一次 worker 尝试写的它，对齐 `job_attempts.attempt_id` | 成员5 对账 | 契约 |
| `chunk_ordinal` | int | 否 | 文档内顺序号，从 0 开始连续递增（`src/pyknora/services/ingest/splitter.py:78-105` 初始化 `ordinal = 0`，仅对非空切片 `+= 1`，故**无空洞、但不保证等于"第几段"**） | 成员3 稳定排序（同分时的确定性排序键） | 契约 |
| `content` | text | 否 | 切片正文 | 成员3 拼上下文 | 契约 |
| `content_hash` | text | 否 | 正文的 SHA-256，复用向量的键 | 只有成员2（`embedding_cache` 主键） | **内部** |
| `page_no` | int | 是 | 页码；非文本格式可能为空 | 成员5 来源定位、前端展示 | 契约 |
| `heading_path` | text | 是 | 标题层级路径 | 成员5 来源定位 | 契约 |
| `char_start` | int | 是 | 在全文中的起始字符偏移 | 成员5 精确引用 | 契约 |
| `char_end` | int | 是 | 在全文中的结束字符偏移 | 成员5 精确引用 | 契约 |
| `token_count` | int | 是 | 估算 token 数 | 成员3 上下文预算裁剪 | 契约 |
| `locator_ref` | text | 是 | 非文本资源的定位句柄 | 成员5 | 契约 |
| `tsvector` | tsvector | 是 | 全文检索向量，由 `to_tsvector('simple', pyknora_cjk_bigram(content))` 在写库后算（`migrations/0007_cjk_bigram_fts.sql`）；**查询侧必须用 `pyknora_cjk_tsquery(text, 'and'\|'or')` 生成 tsquery，不能再用 `plainto_tsquery('simple', …)`** | 成员3 的关键词（FTS）分支——但**不要自己写 SQL**，走 §3.5 的 `search_keyword` | 契约 |
| `embedding` | vector | 是 | 语义向量 | 成员3 的向量召回分支 | 契约 |

三条必须记住的事实：

- **`embedding` 的维度不是全局固定值**。物理列宽在建库时由 `scripts/migrate.py:26` 读 `settings.embedding_dim` 注入 `\set dim <n>`，再喂给 `migrations/0001_init.sql` 的 `vector(:dim)`；一旦建完表，列宽就固定了，改维度必须重跑迁移。而**每一行向量属于哪个向量空间，唯一权威记录是 `index_builds.embedding_dim`**（视图把 `b.embedding_model_key` / `b.embedding_dim` / `b.chunk_size` 以及 `d.kb_id` 都投影成了列）。**读的时候用行内的 `embedding_dim`，不要去读 `settings.embedding_dim`**——后者是"我现在的配置"，不是"这行数据用的配置"，两者在改过配置的库上会不一致（`scripts/seed_for_m3.py:153` 就是拿两者比对并直接报错）。
- **`chunks` 表没有 `deleted_at`、也没有 `state`**。可见性完全由 `documents` 与 `index_builds` 决定，不在这张表里。想在 `chunks` 上筛"已删除"是筛不出来的。
- 表上有 `UNIQUE (workspace_id, document_id, index_version, attempt_id, chunk_ordinal)`。同一次尝试里序号不能重复，所以"重跑一次作业"必然产生新 `attempt_id`，不会原地覆盖。

---

## §3 共享物之二：`C2` 读契约的四个 Protocol 签名

这节解决：成员3 / 成员5 不写 SQL 也能取数，但必须知道**每个方法失败时是返回 `None` 还是抛异常**——写错这一条，上线后就是 500 而不是 404。

定义位置：`src/pyknora/services/contracts/reads.py`（`Protocol` 只此一处，改签名只改这个文件）；实现位置：`src/pyknora/db/repos/reads.py`。

### 3.1 `DocumentRepo.get_active`

```python
async def get_active(self, scope: Scope, document_id) -> ActiveDocument | None
```

- **参数**：`scope` 必填第一个参数，无默认值；`document_id` 在 Protocol 里未标注类型，实现在 `SqlDocumentRepo` 里是 `UUID`。
- **返回**：`pyknora.schemas.document.ActiveDocument`，字段为 `document_id` / `workspace_id` / `kb_id` / `active_document_version_id` / `active_build_id` / `active_index_version` / `generation` / `row_version` / `created_at`。
- **失败语义**：**不抛异常**。文档不存在、已删除（`deleted_at` 非空）、跨 workspace，三者统一返回 `None`——这是刻意的，用来避免用状态码探测存在性。
- **语义红线**：返回的是「活动指针（`pointer`）所指的那一版」，**不是最新写入的那一版**。任何调用方都不许自己写 `SELECT ... ORDER BY created_at DESC LIMIT 1`。

### 3.2 `ChunkRepo.list_active_chunks`

```python
async def list_active_chunks(
    self, scope: Scope, document_id, *, limit: int = 100, offset: int = 0
) -> Sequence[ActiveChunk]
```

- **参数**：`limit` / `offset` 是 **keyword-only**（有 `*` 分隔），位置传参直接 TypeError。默认 `limit=100`、`offset=0`。**没有 cursor 参数**——分页是 offset 式，不是游标（`cursor`）式。
- **返回**：`Sequence[ActiveChunk]`，按 `chunk_ordinal` 升序。`ActiveChunk`（`pyknora.schemas.document`）**19 个字段**：`chunk_id` / `workspace_id` / `kb_id` / `document_id` / `build_id` / `index_version` / `attempt_id` / `chunk_ordinal` / `content` / `page_no` / `heading_path` / `char_start` / `char_end` / `token_count` / `locator_ref` / `embedding_model_key` / `embedding_dim` / `chunk_size` / `active_document_version_id`。
- **失败语义**：**不抛异常、也不返回 `None`**。跨区、文档不存在、staging 未发布，一律返回**空序列**（`[]`）。所以 `if chunks is None` 是永远不成立的分支，`if not chunks` 才是对的。
- **语义红线**：活动构建之外的数据永远读不到。视图的 `WHERE` 是四条合取（`migrations/0001_init.sql:231-234`）：`d.active_build_id = b.build_id`、`b.state = 'active'`、`d.deleted_at IS NULL`、`c.embedding IS NOT NULL`。`staging` 与 `retired` 的 build 里的切片在结构上不可见——注意最后一条 `embedding IS NOT NULL`，它意味着**"切片写了但向量还没算出来"的行在发布前后都读不到**，向量化失败的批次不会以半成品形态漏到检索里。
- **注意**：`ActiveChunk` **不含 `tsvector`、也不含 `embedding`**（视图这两列都有，模型刻意没带）。**不要写"逐列对应"的假设**，也不要指望从这里拿到向量自己去算距离——需要向量走 §3.4 的 `search_similar`，需要关键词召回走 §3.5 的 `search_keyword`。

### 3.3 `IndexBuildRepo.get`

```python
async def get(self, scope: Scope, build_id) -> IndexBuild | None
```

- **参数**：`scope` 必填第一个参数；`build_id` 实现里是 `UUID`。
- **返回**：`pyknora.schemas.document.IndexBuild`，**15 个字段**：`build_id` / `workspace_id` / `document_id` / `index_version` / `attempt_id` / `embedding_model_key` / `embedding_dim` / `chunk_size` / `chunk_overlap` / `normalize` / `chunk_count` / `state` / `created_at` / `activated_at` / `retired_at`。
- **失败语义**：不抛异常，不存在或跨区返回 `None`。
- **语义红线**：`state` 的取值只有三个——`staging` / `active` / `retired`（常量定义在 `src/pyknora/services/ingest/publish.py`）。**不要从 `state` 去判断"数据可不可读"**，可读性只由视图回答；`state='active'` 但 `documents.active_build_id` 已指向别的 build 的情况，在发布流程里是瞬时的。

### 3.4 `ChunkSearchRepo.search_similar`

```python
async def search_similar(
    self, scope: Scope, kb_id, query_vector: Sequence[float], *,
    limit: int = 10, document_id=None,
) -> Sequence[ScoredChunk]
```

- **为什么需要它**：`ActiveChunk` 不带向量，所以"拿向量算距离"这件事必须有另一个口子。这个方法是**唯一的向量检索原语**，你们不要自己写 SQL 打视图——写漏 `workspace_id` 谓词就是一次越权，而这里有现成的。
- **`limit` / `document_id` 是 keyword-only**，位置传参 TypeError。`document_id` 传 `None` 表示"整个 KB"，传具体值表示"只在这个文档内检索"。
- **返回**：`Sequence[ScoredChunk]`，按距离升序（最近的在前）。`ScoredChunk` **16 个字段** = `ActiveChunk` 去掉 `char_start` / `char_end` / `token_count` / `chunk_size`（检索不需要），加一个 `distance`。
- **`distance` 是余弦距离，不是相似度**：0 表示方向完全一致，2 表示完全相反。**故意不叫 `score`**——叫 score 就会有人当成"越大越相关"去排序或加阈值。要转相似度写 `1 - distance`，这个减法请写在调用方，别藏在这一层。
- **失败语义**：跨区 / KB 不存在 → **空序列 `[]`**，不抛异常。查询向量维度与库内不一致 → 抛 `EmbeddingDimensionError`（`details["expected"]` / `details["actual"]` 两个键），因为它不是"没找到"，是调用方配置错了。
- **维度护栏在 DDL 层，不在这里**：`chunks.embedding` 声明为 `vector(:dim)`，整表维度是一个常量，所以"同库混存 512 与 1024"在 `INSERT` 阶段就被 PG 拒了（实测 `DataError: expected 512 dimensions, not 1024`）。因此本方法里的维度检查是**纵深防御**，主防线是列类型。换模型要换维度必须走迁移（新列或新表），不能插不同长度的向量。这条已由 `tests/integration/test_search_repo.py::test_mixed_dimensions_are_forbidden_by_ddl_not_by_app` 钉住。

### 3.5 `ChunkRepo.search_keyword`（中文修好之后新增的关键词召回原语）

```python
async def search_keyword(
    self, scope: Scope, kb_id, query_text: str, *, limit: int = 10,
) -> list[ActiveChunk]
```

- **为什么现在有它了**：上一版本文里写的是「关键词(FTS)召回成员2 还没提供原语，要做请来找我」。现在它有了——`src/pyknora/db/repos/reads.py` 的 `SqlChunkRepo.search_keyword`。**同样不要自己写 SQL 打视图**，理由和 §3.4 一样：写漏 `workspace_id` 谓词就是一次越权。
- **它替你做的降级**：内部是**两阶段**——先用 `AND` 把所有 bigram 同时命中（精确、召回低），不够 `limit` 条再用 `OR` 补（宽松、召回高）。两阶段结果按 `chunk_id` 去重并保持 `ts_rank_cd` 降序，所以**返回顺序仍是全局相关性降序，但你不被告知某一条是 AND 阶段还是 OR 阶段来的**。若你需要这个区分（例如只想要精确命中），跟我说，我加一个可选参数，而不是你在外面重写一遍 SQL。
- **`query_text` 传原始查询串，不要自己分词**。分词在数据库侧由 `pyknora_cjk_bigram()` 完成（汉字取相邻 2 字、英文数字整词、单字保留、标点当分隔符）。你要是先在 Python 里切一遍再传进来，会被当成一整段再切一次，结果是乱码级 token。
- **超长查询会被截到 256 字符**（`reads.py::_MAX_KEYWORD_QUERY_CHARS`）。这不是保守，是必需：中文按「字数≈词元数」，而 `to_tsquery` 是递归下降解析——实测 5000 字 → 4999 词元/200ms，**20000 字直接 `stack depth limit exceeded`**，经 API 就是 500。旧实现（`plainto_tsquery('simple', …)`）把整段汉字当 1 个词元所以不会炸，是 bigram 引入的新风险面。截断只影响关键词这一路，向量路仍用完整问题。
- **限长不能只靠 API schema**：封顶首先落在仓库层（`_MAX_KEYWORD_QUERY_CHARS = 256`），因为任何调用方（含未来的内部任务）都可能绕过 schema 直接调仓库。**第二道已经在 API 层加好**（2026-10-30，经用户确认后越域改了成员3 的 `src/pyknora/schemas/query.py`）：`QueryRequest.query` 现在是 `Field(min_length=1, max_length=1000)`——上限比仓库层宽松，以免砍掉正常长问句；`min_length=1` 是因为空问题会走到 embedding 但语义为空，返回一堆无关片段比 422 更糟。对应用例 `tests/api/test_api_query.py::test_query_rejects_overlong_and_empty_query`（1001 字与空串都必须 422，且向量路一次都没被调用）。
- **失败语义**：跨区 / KB 不存在 / 无命中 → **空列表 `[]`**，不抛异常。纯标点查询 → `[]` 不报错；单个汉字查询 → `[]`，因为 bigram 最短有效长度是 2 个汉字（已知边界：`pyknora_cjk_bigram('税')` 只产出 `税` 一个词元，而多字词的索引里没有 `税` 这个 token，所以对不上；要单字召回得再叠一层 `ILIKE` 或 `pg_trgm`，本轮没做）。
- **参数化**：`query_text` 走绑定参数，查询串里的 `& | ! : * (` 等 tsquery 元字符在分词阶段被当分隔符丢掉，不构成注入面（`tests/integration/test_search_repo.py::test_keyword_search_ignores_tsquery_metacharacters`）。
- **和 §3.4 的关系**：两者是并行的召回通道，融合（RRF）在成员3 那层做。`search_keyword` 只给候选，不给分数——`ts_rank_cd` 的值不对外暴露，理由同 §3.4 不暴露原始距离。

---

## §4 共享物之三：`EmbeddingProfile` 的字段名与来源

这节解决：成员5 重建索引时要回答「这篇旧索引用什么参数建的」，成员3 拼查询向量时要与之对齐——答案全在这五个字段。

定义位置：`src/pyknora/schemas/kb.py`，模型是 `frozen=True`（Pydantic 冻结，构造后不可改）。

| 字段名 | 类型 | 谁固化 | 为什么必须是创建时固化、之后只读 |
| --- | --- | --- | --- |
| `embedding_model_key` | `str` | 创建 KB 时写入 | 换模型 = 换向量空间。旧向量和新向量不能混算相似度，必须能分辨 |
| `embedding_dim` | `int`（`gt=0`） | 创建 KB 时写入 | 维度不符时库层直接拒写，是"禁止混用 embedding 空间"的数据库级护栏 |
| `chunk_size` | `int`（`gt=0`） | 创建 KB 时写入 | 切片边界变了，`char_start`/`char_end` 的语义跟着变，旧引用无法与旧正文对齐 |
| `chunk_overlap` | `int`（`ge=0`） | 创建 KB 时写入 | 同上；overlap 影响哪些文本出现在多个切片里 |
| `normalize` | `bool`（默认 `True`） | 创建 KB 时写入 | 归一化与否改变向量长度，进而改变余弦相似度的可比性 |

**为什么必须固化**：如果这五个值只能从全局 `settings` 读，那么"这个 KB 当初用 512 维建的、现在 `settings` 改成 1024 维"就变成不可判定——重建索引（reindex）和迁库时无法回答"旧索引用什么参数建的"。代码里已经把这个答案落在两处冗余上：`index_builds` 表存了同样五个字段（`embedding_model_key` / `embedding_dim` / `chunk_size` / `chunk_overlap` / `normalize`），那是**每次构建的事实快照**。重建时读 `index_builds`，不要读 `settings`。

**一个已补齐的缺口（不用再按"取不到"写了）**：`index_builds` 表有 `normalize bool NOT NULL DEFAULT true` 这一列。初版 `pyknora.schemas.document.IndexBuild` 漏了这个字段、`SqlIndexBuildRepo.get` 也没查，现已补上（`normalize: bool = True`，加性变更，不破坏任何调用方）。**现在 `IndexBuildRepo.get` 能拿到 `normalize`**，成员5 不必自己再 `SELECT`。

### 4.1 当前未决缺口（需要成员1 答复）

PRD 里 `KnowledgeBase` 只有 `embedding_model_id` 和 `strategy` 两个字段，而成员2 需要上面五个。这不是文档措辞问题，是 `C1` 拿不到参数就没法建索引。

| 方案 | 做法 | 代价 | 谁要多做 |
| --- | --- | --- | --- |
| 甲 | 在 `knowledge_bases` 表上补 `embedding_model_key` / `embedding_dim` / `chunk_size` / `chunk_overlap` / `normalize` 五列 | 偏离 PRD 的字面定义 | 成员1 的建表与配置写入各加一点 |
| 乙 | `embedding_model_id` 指向一张 `embedding_model_profiles` 表，`C1` 按 ID 查参数 | 多一张表 | 成员1 多一张表；但 PRD 定义不变，将来加模型只需插行 |

**成员2 目前状态**：用 `FakeKbRepo`（`src/pyknora/db/repos/reads.py`）顶着——**数据是假的、schema 是真的**，它返回的就是 `EmbeddingProfile`。按方案甲（超集）的形态写，因为乙只是在甲外面多一层查表。等成员1 答复后，替换成本应当只有依赖注册那一行。

**`C1` 的真实签名**（从代码抄的，注意返回类型是 `object | None` 而非 `EmbeddingProfile | None`）：

```python
async def get_embedding_profile(self, scope: Scope, kb_id) -> object | None
```

失败语义同 `C2`：KB 不存在、跨区、已删除，统一返回 `None`，不抛异常。调用方请按 `EmbeddingProfile` 的属性名取值（`embedding_dim` 等），类型标注宽是为了避免 `schemas` 与 `contracts` 循环导入。

**`FakeKbRepo` 的越权 bug 已修**：初版实现是 `return self._profiles.get(kb_id)`——`scope` 收到了但完全没用，跨区查询会返回别人的 profile 而不是 `None`。现在改成按 `(workspace_id, kb_id)` 存：`self._profiles: dict[tuple[UUID, UUID], object]`，登记用 `add(scope: Scope, kb_id: UUID, profile: object)`。注意 `kb_id` **跨区可以重复**（两个 workspace 各自 `aaaaaaaa-...` 完全合法），用 `kb_id` 单键会互相覆盖——这已被 `tests/unit/test_fake_kb_repo.py::test_same_kb_id_in_two_workspaces_are_distinct` 钉住。真实 `kb_repo` 落地的验收项里仍必须包含"`WORKSPACE_B` 的 scope 查 `WORKSPACE_A` 的 kb_id 得到 `None`"这一条。

---

## §5 给成员3 的三条使用约束

这节解决：成员3 要用这些数据做检索，而下面三条每一条写错都能让检索「看起来正常、结果是错的」。

**第一条：只读 `v_active_chunks` 视图，不要直接查 `chunks` 表。**

直接查表会读到 `staging` 的中间数据（一次重跑就产生了 `state='staging'` 的 build）和历史 `retired` 数据，结果是**检索结果翻倍且不报错**——用户看到重复段落，日志里一行异常都没有。视图已经把安全性做进了结构，你**不需要**再写 `deleted_at IS NULL`、也不需要筛 `index_version`（写了不错，只是重复）。

但有一件**必须由你做**的事，视图无法替你做：**查询向量必须用行内的 `embedding_model_key` / `embedding_dim` 去校验**。视图给出的 `embedding_dim` 是"这行数据属于哪个向量空间"，你拼出来的查询向量若与它不同维，`<=>` 运算会直接报错（维度不一致），而不是给你一个错的结果。换言之：视图保证"不会读到旧 build 的数据"，但不保证"你的查询向量和它同维"——后者只能由你在 SQL 或服务层显式对齐。

补充（同属第一条的延伸，但很容易踩）：`list_active_chunks` 返回的 `ActiveChunk` **不带 `embedding` 和 `tsvector`**。所以你**不要**自己写 SQL 去打视图取这两列——向量召回走 §3.4 的 `search_similar`（它已经把 `workspace_id` 谓词写好了）；关键词召回走 §3.5 的 `search_keyword`（同样带谓词）。**两份各写一份的结果是：一份漏了 workspace 谓词、另一份没漏，而两者返回值长得一样。**

**第二条：`Scope` 是必填参数，`scope.owns(None)` 返回 `False`。**

`Scope`（`src/pyknora/core/scope.py`）是 `@dataclass(frozen=True, slots=True)`，两个字段：`workspace_id`、`principal_id`，`__post_init__` 里校验必须都是 `UUID`（传字符串直接 `TypeError`）。`owns(owner_id)` 的完整实现是：`None` 先返回 `False`，否则 `owner_id == self.workspace_id or owner_id == self.principal_id`——注意是 **OR**，所以"行的归属字段写的是 workspace"和"写的是 principal"都算可见，两种粒度都支持。fail-closed 的部分只在 `None` 那一个分支上，但它是这三行里最重要的：**"缺失 owner 字段被读成属于所有人"是同类项目的真实事故**，不要把它当成多余的防御代码删掉。三个读方法里 `scope` 都是第一个位置参数且无默认值（`contracts/reads.py` 注释里的红线 R3），这不是风格问题，是为了让"忘记传 scope"在类型检查阶段就暴露。

**第三条：跨 workspace 的 `document_id` 查回来必须是空列表，不是「空字段的对象」。**

正确形状：`get_active` → `None`；`list_active_chunks` → `[]`（长度 0 的空序列）。错误形状（也是测试最该钉住的）：返回一个字段全为 `None` / 空串的 `ActiveChunk` 假对象，或者"过滤发生在 Python 里而不是 SQL 里"——后者的典型症状是**返回条数少于 `limit` 但调用方以为自己分页翻到了尾**，因为名额已经被别人 workspace 的行挤占了，`LIMIT` 是在过滤之前生效的。所以 `SqlChunkRepo` 把 `workspace_id` 写进 `WHERE`，不是"取回来再筛"。验收时断言 `len(chunks) == 0`，不要断言"返回值的 `.content` 是空字符串"——前者是契约，后者是巧合。

---

## §6 给成员5 的两条约束

这节解决：成员5 要做重建索引（reindex）与来源追踪（lineage），而这两件事都踩在成员2 的事务语义上。

**第一条：重建索引必须走同一套 `C4`（入队）/ `C6`（发布）语义，不要自己写第二条切换路径。**

真实签名（从代码抄的，与旧计划书的语义级描述不同）：

```python
# C4 入队：src/pyknora/services/queue/jobs.py
async def enqueue(
    session: AsyncSession, *, scope: Scope, document_id: UUID,
    job_type: str, payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> IngestionJobRow

# C6 发布：src/pyknora/services/ingest/publish.py
@dataclass(slots=True)
class PublishManifest:
    build_id: UUID
    document_version_id: UUID
    chunk_count: int
    embedding_model_key: str
    embedding_dim: int

# ClaimedJob（src/pyknora/services/queue/jobs.py:37-48）——publish 的三个校验凭据就在里面
# job_id / workspace_id / document_id / job_type / attempt / lease_epoch / attempt_id / payload
async def publish(session: AsyncSession, *, job: ClaimedJob, manifest: PublishManifest) -> IndexBuildRow
```

- `enqueue` **必须在调用方的事务内调用**，与业务行的写入同生共死；同 `idempotency_key` 重复入队返回既有行，不新建（同 key 不同参数抛 `IDEMPOTENCY_CONFLICT`）。返回的是 `IngestionJobRow`，**不是 `job_id`**。传 `None` 时 key 会被拼成 `f"{job_type}:{document_id}:{uuid4()}"`——**等于没有幂等**，所以重建索引一定要自己给一个稳定的 key（例如 `reindex:{document_id}:{target_index_version}`），否则重试会造出多条作业。
- `publish` 抛的是 `AppError`，可能码值有 `RESOURCE_NOT_FOUND`（document 不见了）、`VERSION_SUPERSEDED`（`documents.desired_document_version_id` 指向另一个版本 / document 在摄取期间被删）、`STALE_ATTEMPT`（`job.lease_epoch` 被别的 worker 接管、build 属于别的 `attempt_id`、build 的 `index_version` 不比 `documents.active_index_version` 新）、`INDEX_INCOMPLETE`（manifest 声称的 chunk 数与库里实际数不符，或为 0——**空 build 一律拒绝发布**）。
- `publish` **不自行 rollback**，由上层 `session_scope` 统一回滚——所以你也别在调用前手动 commit。
- 同一 build 重复发布是**幂等且不报错**的：`build.state == 'active'` 且 `document.active_build_id == build.build_id` 时直接返回该 `IndexBuildRow`，不重写任何字段。
- 发布事务的六步顺序不可换：① `SELECT ... FOR UPDATE` 锁 `documents` 行 → ② 校验 `job.lease_epoch` / `desired_document_version_id` / `build.attempt_id` / `chunk_count` / `index_version` 单调性 → ③ 锁内算下一个 `index_version` → ④ 旧 build 置 `retired` + `retired_at` → ⑤ 新 build 置 `active` + `activated_at` + 回填 `chunk_count`，同时改 `documents` 三个指针（`active_build_id` / `active_index_version` / `active_document_version_id`）与 `generation`、`row_version`，并把 `document_versions.parse_status` 置 `succeeded` → ⑥ `ingestion_jobs.status` 置 `succeeded` + `queue.finish_attempt` + 写 `outbox`（`event_type='document.index_published'`）。

自己另写一条"直接 UPDATE `documents.active_build_id`"的切换路径，会绕过"先退休再上新"和 `index_version` 单调性校验，结果是视图在一个窗口里看到两个 build，或者旧索引把新索引覆盖掉。

**第二条：`citations` 表不在 `v_active_chunks` 视图里，需要自己再 join 一次 `document_versions`。**

视图只投影了 `d.active_document_version_id` 这一列（`documents.active_document_version_id`），它给你的是**锚点**，不是版本行的全部字段。要落 `citations` 需要的内容（`content_hash` / `object_key` / `byte_size` / `ingestion_source` / `created_by_principal_id` / `parse_status`）必须回到 `document_versions` 表按 `(workspace_id, document_version_id)` join。`sources` / `source_versions` 同理，由你自己建、自己 join，成员2 不把它们放进视图（视图列清单变更会让成员3 的调用点跟着动）。

---

## §7 与 `docs/plans/成员2执行计划.md` §4 不一致的地方

这节解决：你可能已经照旧计划书写了代码，这里逐条列出代码与计划书的差异，**以本文（代码）为准**。

| 项 | 旧计划书 §4 的写法 | 代码实际 | 影响 |
| --- | --- | --- | --- |
| 方法归属 | 一个 `DocumentReader` 同时含 `get_active` / `list_active_chunks` / `fetch` | 拆成四个 Protocol：`DocumentRepo` / `ChunkRepo` / **`ChunkSearchRepo`（新增，向量检索原语）** / `IndexBuildRepo`，另有 `KbRepo` | 依赖注入按单个 Protocol 声明，不要按合并类声明 |
| 分页 | `list_active_chunks(scope, document_id, limit, cursor) -> (Sequence, str \| None)` | `list_active_chunks(scope, document_id, *, limit=100, offset=0) -> Sequence` | 没有 next_cursor，分页是 offset 式 |
| 向量检索 | 计划书未定义（只有一句 `fetch(scope, kb_id, top_k)`，未实现） | **新增 `ChunkSearchRepo.search_similar(scope, kb_id, query_vector, *, limit=10, document_id=None) -> Sequence[ScoredChunk]`**，`distance` 是余弦距离 | 这是成员3 唯一的向量召回入口，别自己写 SQL 打视图 |
| 关键词检索 | 计划书未定义（`search_hybrid` 内部直接 `plainto_tsquery`，无独立原语） | **新增 `ChunkRepo.search_keyword(scope, kb_id, query_text, *, limit=10) -> list[ActiveChunk]`**，内部 AND→OR 两阶段降级 | 这是成员3 唯一的关键词召回入口；**查询串不要自己分词**，分词在库侧 |
| `IndexBuildRepo.get` | `get(build_id)`，无 scope | `get(scope, build_id)` | 旧写法违反计划书自己的 R3 红线（scope 必填第一个参数） |
| `ActiveChunk` 位置 | `pyknora.schemas.chunk` | `pyknora.schemas.document`（`schemas/chunk.py` 里是切片器输出 `Chunk`） | import 路径不同 |
| `ActiveChunk` 字段数 | 与视图列「逐列对应，不多不少」 | `chunks` 17 列 → **视图 21 列**（去掉 `content_hash`，加 `kb_id` / `embedding_model_key` / `embedding_dim` / `chunk_size` / `active_document_version_id`）→ `ActiveChunk` **19 个字段** = 视图减去 `tsvector` 与 `embedding` | 「逐列对应」这句话在当前代码里不成立，差集就在这两列；`kb_id` 是后补的，早期版本没有 |
| `C6` 返回值 | `publish(ctx) -> None` | `publish(session, *, job, manifest) -> IndexBuildRow` | 需要 build 结果时不用再查一次 |
| `C4` 返回值 | `-> job_id` | `-> IngestionJobRow` | 取 id 用 `.job_id` |
| `C5` 签名 | `claim(session) -> IngestionJob \| None` | `claim(session, *, worker_id, lease_seconds=90) -> ClaimedJob \| None` | `worker_id` 必填；`attempt_id` 由 claim 现场生成 |
| `C1` 返回类型 | `-> EmbeddingProfile \| None` | `-> object \| None` | 属性名仍是 `EmbeddingProfile` 的字段名 |
| 视图 DDL | §4.2 的版本缺 `AND c.embedding IS NOT NULL`（§7.4 有） | `migrations/0001_init.sql` 有这一行（红线 R1） | 以 `0001_init.sql` 为准 |
| `documents` 列 | 上位文档 §4.3 无 `active_build_id` | 有，且是视图 join 的锚点 | 计划书 §7.4 已注明这是增强 |

---

## §8 需要你们回的两句话

| 谁 | 回什么 | 截止 |
| --- | --- | --- |
| 成员3 | 「§2 列清单、§3 五个方法（含新增的 §3.5 `search_keyword`）、§5 三条约束」同意 / 改 X | 今天 |
| 成员5 | 「§6 两条约束 + §3 五个方法够不够用」同意 / 改 X | 今天 |
| 成员1 | §4.1 选甲还是选乙 | 今天（这条卡住我的 `FakeKbRepo` 替换） |

**v2 额外提醒成员3**：如果你已经按 v1 写了 `plainto_tsquery('simple', ...)` 或自己拼 FTS SQL，请**删掉改成 `search_keyword`**。旧写法在中文上返回 0 行且不报错——现象是「融合之后结果变差」，很容易误判成 RRF 权重问题。
