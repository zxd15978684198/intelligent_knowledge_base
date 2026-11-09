# eval/ · Day3 可信 RAG 离线评测

这条流水线的目的只有一个：在**固定语料快照 + 固定配置**下，用 HTTP 打真服务，产出一组
可复现、可归因的数字。任何写进文档/汇报的指标，都必须能在这里找到出处（哪个 `summary.json`、
哪份配置、哪个 commit）。

runner 是 HTTP-only 的（不 import 被测模块、不直连数据库）——它看到的和真实调用方看到的一致，
否则"评测通过"证明不了"线上能用"。

## 1. 流水线的六个产物

| # | 文件 | 谁生成 | 作用 | 入库 |
| --- | --- | --- | --- | --- |
| 1 | `eval/corpus_manifest.json` | 人工登记，`corpus.py --verify-only` 复核 | 5 份语料的身份：文件名 / 字节数 / sha256 / `target`（`main` 4 份，`forbidden` 1 份） | 是 |
| 2 | `eval/corpus.py` | — | 语料准备与校验：`--prepare` 幂等落位 5 份语料并写清单、`--verify-only` 按清单重算 sha256（不一致 exit 1）、`--list` 只打印清单 | 是 |
| 3 | `eval/build_eval_set.py` | — | 生成评测集：`--build`（默认）走 API 回填 `gold_chunks` 并写产物；`--emit` 离线重生成（种子来自脚本，gold 复用 `--from` 指向的既有产物，不发 HTTP）；`--check` 只校验已有产物的配额/字段/gold，不发 HTTP | 是 |
| 4 | `eval/eval_set_v0.1.jsonl` | `build_eval_set.py --build` | 30 条样本：8 类 category，`expect` ∈ {answer, abstain, deny} | 是 |
| 5 | `eval/make_configs.py` → `baseline_config.yaml` / `candidate_config.yaml` | `make_configs.py` | 两组实验口径。**yaml 是生成物，不手改**；两份之间只允许一个主因素不同 | 是 |
| 6 | `eval/run_eval.py` | — | runner：登录 → 逐条打 `/query` 与 `/chat` → 写 `raw.jsonl` / `summary.json` / `report.md` | 是 |

8 类 category 的后三类是安全组，口径按 `build_eval_set.py` 的 `EXPECT_OF`：
`injection`（提示注入）/ `poison`（污染来源）的 `expect == "abstain"`（拒答）；
`privilege`（越权）的 `expect == "deny"`（403/404 探针）。`abstain`（没证据）与
`deny`（没权限）**绝不混用**——harness 只有 privilege 走越权探针，注入/污染一律拒答。
老样本 `deny-01` / `deny-02` 保留原 id，只把 `category` 归到 `privilege`。

`eval/results/` 是**输出目录**，不是产物：每次运行一个子目录（建议 `base-<日期>` / `cand-<日期>`），
按计划约定**不入库**。`.gitignore` 已追加 `eval/results/` 与 `eval/reports/`（2026-10-30），
自查用 `git check-ignore -v eval/results/x.json`（返回 `.gitignore:NN:eval/results/` 且 exit 0 才算生效）。
注意 `eval/` 整体仍是**未跟踪目录**，`git status` 只显示一行 `?? eval/`；要确认某个文件是否会被入库，
用 `git status --short --untracked-files=all eval` 逐文件看，别按目录名猜。入库只加上面表里的六个产物。

## 2. 怎么跑（基线 / 候选）

前置：PG 已起、迁移与种子已跑、评测库已按 `.tmp_ref/eval_setup.json` 建好
（`kb_id` / `workspace_id` / 3 篇文档 / `forbidden` 那套），API 起在 `127.0.0.1:8010`
（`uv run --frozen uvicorn pyknora.api.main:app --host 127.0.0.1 --port 8010`）。

```powershell
# 0) 语料校验（sha256 对不上就别往下跑，数字没有意义）
uv run --frozen python eval/corpus.py --verify-only

# 1) 重新生成两份配置（改了任何取值来源之后都要跑）
uv run --frozen python eval/make_configs.py

# 2) 只校验配置，不发请求：loader 校验 + 差异 + drift
uv run --frozen python eval/make_configs.py --check

# 3) 公平性守卫：两份必须恰好一个主因素不同，否则 exit 2
uv run --frozen python eval/run_eval.py --compare-config eval/baseline_config.yaml eval/candidate_config.yaml

# 4) A 组（基线）：**服务端**用 baseline 策略起 API，然后跑
uv run --frozen python eval/run_eval.py --config eval/baseline_config.yaml --repeats 3 --out eval/results/base-20261030

# 5) B 组（候选）：**把服务端改成 candidate 并重启 API**，再跑
uv run --frozen python eval/run_eval.py --config eval/candidate_config.yaml --repeats 3 --out eval/results/cand-20261030
```

登录凭据只走 `--username/--password` 或 `PYKNORA_EVAL_USERNAME` / `PYKNORA_EVAL_PASSWORD`。
**绝不写进 yaml**：配置是要入库的文件，而评测账号往往是本机管理员账号。

### 两个必须知道的坑（都是实测踩到的）

1. **策略切换靠服务端环境变量 + 重启，不靠 runner 的参数。**
   `run_eval.py` 的 `/query` 请求体只有 `{"query": ..., "top_k": ...}`（没有策略字段），
   所以 `PYKNORA_RETRIEVAL_STRATEGY=candidate uv run python eval/run_eval.py ...` 这种写法
   只改了**客户端进程**的环境，服务端仍是旧策略——两轮会跑出同一组数字，而且看起来"候选没收益"。
   正确做法：`PYKNORA_RETRIEVAL_STRATEGY=candidate` 给 **uvicorn** 那个进程，重启后再跑。
2. **两份配置的 `experiment_id` 相同，所以必须用 `--out` 分开。**
   `experiment_id` 不在 `PRIMARY_FACTOR_FIELDS` 里，两份写不同值会被守卫判成"非主因素差异"而 exit 2；
   它是"这次 A/B"的名字，不是"这一轮"的名字。默认输出目录是 `eval/results/<experiment_id>`，
   不传 `--out` 时第二组会覆盖第一组。

## 3. 字段含义

所有值都取自真实来源，没有一处是手填的（`make_configs.py` 里可逐项对到代码）。

| 字段 | 真实来源 | 说明 |
| --- | --- | --- |
| `experiment_id` | 生成器常量 `day03-rag-ab` | 这次 A/B 的标识；两份必须相同（见上面第 2 个坑） |
| `app_commit` | `git rev-parse HEAD` | runner 会拿运行时 HEAD 比对，不一致在报告里告警（`app_commit_mismatch`） |
| `prompt_version` | `src/pyknora/services/rag/answer.py` 的 `PROMPT_VERSION` | 报告里"这批答案出自哪版提示词"的唯一凭据；改提示词必须跟着变 |
| `eval_dataset` | `eval/eval_set_v0.1.jsonl` | 相对路径，`resolve_dataset_path` 依次试 cwd / 配置目录 / 其上一级 |
| `snapshot.workspace_id`、`snapshot.kb_id` | `.tmp_ref/eval_setup.json` | 这轮数字跑在谁的数据上 |
| `snapshot.documents[]` | 同上 | 每篇的 `document_id`、`active_document_version_id`、`active_build_id`、`active_index_version`、`chunks`——用于证明"这批数字对应哪些切片"，也是切片被重传/退休后能追溯的依据 |
| `chat_model`、`chat_base_url`、`chat_timeout_s` | `pyknora.core.settings` 的 `settings` 对象 | 运行时真实值。**不在 eval/ 里读 `.env`**：那是第二个真相来源，还会把密钥带进日志 |
| `embedding_model_key`、`embedding_dim`、`embedding_backend` | 同上（`settings`） | 评测默认真后端 `local` + `BAAI/bge-small-zh-v1.5` / 512 维 |
| `retrieval_strategy` | `settings.RETRIEVAL_STRATEGY_BASELINE/CANDIDATE` | **唯一的实验主因素**：A 组 `baseline`，B 组 `candidate` |
| `query_top_k` | 生成器常量 `5` | `/query` 请求的 top_k；同时也是 recall@5 / precision@5 的口径来源 |
| `chat_top_k` | 生成器常量 `6` | `/chat` 取的上下文条数 |
| `reranker` | 生成器常量 `None` | 本轮不启用（计划 §12 的不做清单），显式写 `null` 而不是省略 |

**没有写 `candidate_k`** 是有意的：`settings` 里没有这个字段，候选扩召倍数由服务端
`_resolve_candidate_k` 按策略结算（`services/retrieval/search.py`）。配置里再写一个数，
就变成"客户端以为的倍数"和"服务端实际用的倍数"两个说法，A/B 的公平性反而说不清。

### 主因素 / 非主因素

`run_eval.py` 的守卫认六个主因素：`retrieval_strategy`、`reranker`、`candidate_k`、
`retrieval_candidate_k`、`rrf_k`、`retrieval_rrf_k`。两份配置**恰好**一个主因素不同才放行；
其余字段（含 `query_top_k`、`chat_top_k`）一旦不同，直接 exit 2——因为那就不是"只改了一个变量"的对照实验。

## 4. 红线：哪些数字必须实测，一个都不许手写

runner 会算八个指标（定义见 `run_eval.py` 的 `METRIC_DEFINITIONS`）：
`recall_at_5`、`precision_at_5`、`citation_accuracy`、`abstain_accuracy`、`deny_interception`、
`retrieval_p95_ms`、`chat_ttft_p95_ms`、`chat_total_p95_ms`。

- 这些值**只能**来自 `eval/results/<run>/summary.json`，且同目录必须有对应的 `raw.jsonl`
  与 `report.md`；三者缺一，这个数字就不许对外引用。
- 引用任何数字必须同时给出三件套：**commit**（`app_commit`）、**哪份配置**（baseline/candidate）、
  **样本量**（`counts.unique_samples` 是评测集条数，`counts.samples` 含 repeats，别混）。
- 没跑就是**待填**。不许写"预计 / 大概 / 约"，也不许从别的环境抄一个凑数。
- `embedding_backend: hashing` 跑出来的结果只能当冒烟（G6）：它证明流程通，不能当效果。
- `--limit N` 的产物不可对外（runner 的 help 里就写着"调试用，结果不可对外"）。
- `repeats=1` 时 `min == max`，这不代表稳定；报告该注明"波动无意义"。
- 一旦出现 deny 泄漏（`deny_interception` 不满且 exit 3），当轮所有指标作废：先修安全项，再谈分数。
- 占位符会被 loader 直接拦下（`PLACEHOLDER_MARKERS` 含 `todo`/`tbd`/`<`/`rev-parse`/`用户填`/`实测`
  等字样），所以也别想用 `TODO` 糊过去——配置里出现"实测"两个字同样判无效。

## 5. 改字段的正确姿势

改 `eval/make_configs.py`（常量或取值来源）→ 重跑 `python eval/make_configs.py` →
`python eval/make_configs.py --check`。**不要手改 yaml**：文件头已声明是生成物，
`--check` 里的 drift 检测会把磁盘内容与生成器重算结果逐字段比对，手改的差异会当场报出来。

`prompt_version` 优先从 `answer.py` import；若该模块此刻不可 import（工作区里可能正被别的任务编辑），
生成器退回到源码里取 `PROMPT_VERSION` 字面量，并在 stderr 打一行警告——静默换源比报错更难查。

## 6. 历史未决项（2026-10-30 已关闭）

| # | 原问题 | 处理结果 |
| --- | --- | --- |
| 1 | `chat_top_k` 两个口径（任务书 6 / 计划 §3.5 写 5） | 统一为 **6**：计划 §3.5 的 yaml 与正文已改，理由「以 `run_eval.py` 的 loader 与任务书为准，2026-10-30 定」写在原处；A/B 可直接开跑 |
| 2 | `eval/results/` 未进 `.gitignore` | 已追加 `eval/results/`、`eval/reports/`（`.gitignore:102` / `:103`），`git check-ignore -v` 已验证 |
| 3 | 计划 §10.3 用客户端环境变量切 candidate（不生效） | 已改写为「改服务端环境变量 + 重启 uvicorn」，并补了反例；`--out` 也一并补上，避免两轮互相覆盖 |

## 7. 常见误用

跑之前先扫一遍。这三条都是"看起来跑了、其实白跑"的类型。

| # | 现象 | 原因 | 正确做法 |
| --- | --- | --- | --- |
| 1 | 第二轮跑完，第一轮的 `summary.json` 不见了 | 两份配置的 `experiment_id` 相同（都是 `day03-rag-ab`），而默认输出目录就是 `eval/results/<experiment_id>/`，后一轮覆盖前一轮 | 每轮都显式给 `--out`：`--out eval/results/base-20261030`、`--out eval/results/cand-20261030`。**不要**靠改 `experiment_id` 区分两轮——它不在 `PRIMARY_FACTOR_FIELDS` 里，两份填不同值会被公平性守卫判成"非主因素差异"，直接 exit 2 |
| 2 | 切了 candidate，两轮数字一模一样，"候选毫无收益" | `PYKNORA_RETRIEVAL_STRATEGY` 是**服务端**配置：`run_eval.py` 的 `/query`、`/chat` 请求体只有 `{"query","top_k"}`，没有策略字段；在 runner 命令前加环境变量只改了客户端进程 | 给 **uvicorn** 那个进程设 `PYKNORA_RETRIEVAL_STRATEGY=candidate` 并**重启**。怎么确认真切了：看 `summary.json` 的 `retrieval_strategy` 与 `retrieval_strategy_source`——只有 `source=response`（服务端回显）才算证据；`source=config` 说明服务端没回显、这个值只是照抄配置，证明不了切换。最硬的证据还是重启后打一发 `/query` 看响应里的策略字段。详见 §2 第 1 个坑、计划 §10.3 |
| 3 | 报告里的指标很好看，但翻 `summary.json` 对不上 | 指标是手写/拍脑袋算的，或引用了 `--limit` 调试跑、`hashing` 后端跑出来的数 | 指标**只认** `eval/results/<run>/summary.json`，且同目录必须有 `raw.jsonl` 与 `report.md`；引用时带齐 commit / 哪份配置 / 样本量三件套（§4）。没跑就是**待填**，不许估 |
