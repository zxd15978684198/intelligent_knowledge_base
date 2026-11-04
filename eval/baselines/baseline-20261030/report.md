# Day3 评测报告（自动生成，勿手改）

- experiment_id：`day03-rag-ab`
- app_commit：`4e16d652cea0f730244571dd92c16a1a112f0be4`
- prompt_version：`pyknora-rag-v1`
- retrieval_strategy：`baseline`
- base_url：`http://127.0.0.1:8030`
- 生成时间：2026-10-30T13:03:45+00:00
- 样本数：20，repeats=1，raw 行数 20
- embedding_backend：`local` / model `BAAI/bge-small-zh-v1.5` / dim 512

## 1 指标

| 指标 | 值 | min | max | 分母/样本数 | 定义 |
| --- | --- | --- | --- | --- | --- |
| recall_at_5 | 1.000 | 1.000 | 1.000 | 15 | 命中 gold 的条数 / gold 条数；gold 为空记 None 且不计入平均 |
| precision_at_5 | 0.200 | 0.200 | 0.200 | 15 | 命中条数 / min(5, 返回条数)；返回 0 条记 0.0 |
| citation_accuracy | 0.356 | 0.356 | 0.356 | 45 | 通过规则核验的引用数 / 被核验引用总数（仅 answer 类样本） |
| abstain_accuracy | 0.667 | 0.667 | 0.667 | 3 | no_evidence 样本中判为 ABSTAINED 的比例（status 缺失时退回拒答文案） |
| deny_interception | 1.000 | 1.000 | 1.000 | 2 | deny 样本中两端点均 403/404 且响应体无片段的比例（安全项，不参与平均） |
| retrieval_p95_ms | 375.140 | 375.140 | 375.140 | 20 | 单次 /query 端到端延迟的 P95（nearest-rank；样本少时仅供参照） |
| chat_ttft_p95_ms | 5078.340 | 5078.340 | 5078.340 | 20 | /chat 首个 token 到达耗时的 P95 |
| chat_total_p95_ms | 5586.000 | 5586.000 | 5586.000 | 20 | /chat 到 done 事件的总耗时 P95 |

## 2 per-category 明细

| 类别 | 样本 | Recall@5 | Precision@5 | 引用准确率 | 拒答准确率 | 拦截率 |
| --- | --- | --- | --- | --- | --- | --- |
| deny | 2 | — | — | — | — | 1.000 |
| exact_term | 4 | 1.000 | 0.200 | 0.217 | — | — |
| fact | 6 | 1.000 | 0.200 | 0.667 | — | — |
| no_evidence | 3 | — | — | — | 0.667 | — |
| synonym | 3 | 1.000 | 0.200 | 0.375 | — | — |
| version | 2 | 1.000 | 0.200 | 0.400 | — | — |

## 3 失败样本（自动归类，需人工复核）

| 样本 | 类别 | repeat | 二级 code | 依据 |
| --- | --- | --- | --- | --- |
| fact-01 | fact | 1 | CITATION_FACT_MISSING | 引用核验 1/2 |
| fact-02 | fact | 1 | CITATION_FACT_MISSING / FACT_MISSING | 引用核验 1/2；expect_facts 未出现在回答正文 |
| fact-04 | fact | 1 | FACT_MISSING | expect_facts 未出现在回答正文 |
| fact-05 | fact | 1 | FACT_MISSING | expect_facts 未出现在回答正文 |
| fact-06 | fact | 1 | CITATION_FACT_MISSING | 引用核验 1/2 |
| exact-01 | exact_term | 1 | CITATION_FACT_MISSING | 引用核验 1/6 |
| exact-02 | exact_term | 1 | CITATION_FACT_MISSING / CITATION_WRONG_DOC | 引用核验 1/5 |
| exact-03 | exact_term | 1 | CITATION_FACT_MISSING | 引用核验 2/6 |
| exact-04 | exact_term | 1 | CITATION_FACT_MISSING / CITATION_WRONG_DOC | 引用核验 1/6 |
| syn-01 | synonym | 1 | CITATION_FACT_MISSING | 引用核验 2/6 |
| syn-02 | synonym | 1 | CITATION_FACT_MISSING / FACT_MISSING | 引用核验 1/2；expect_facts 未出现在回答正文 |
| syn-03 | synonym | 1 | FACT_MISSING | expect_facts 未出现在回答正文 |
| noev-02 | no_evidence | 1 | OVER_RETRIEVAL | 无证据题却给了实质回答 |
| ver-01 | version | 1 | CITATION_FACT_MISSING | 引用核验 1/4 |
| ver-02 | version | 1 | FACT_MISSING | expect_facts 未出现在回答正文 |

## 4 版本记录（配置全字段）

| 字段 | 值 |
| --- | --- |
| `experiment_id` | day03-rag-ab |
| `app_commit` | 4e16d652cea0f730244571dd92c16a1a112f0be4 |
| `prompt_version` | pyknora-rag-v1 |
| `eval_dataset` | eval/eval_set_v0.1.jsonl |
| `snapshot.workspace_id` | d2c28dec-61ca-4799-82c2-e3ba4b7e037e |
| `snapshot.kb_id` | 1556633e-fff3-4a27-8731-adc00d2ff5eb |
| `snapshot.documents` | [{"active_build_id": "8a2b50a2-3947-4676-9b17-03b2f8ef6266", "active_document_version_id": "bb4becf1-b34a-49cd-a70e-9be266ef8568", "active_index_version": 1, "chunks": 61, "document_id": "8fc2223d-11af-4c49-b3dc-ff842d362cba"}, {"active_build_id": "41fc983c-fce7-418e-a4d9-83bf6739bcaa", "active_document_version_id": "0548e2e3-ba92-4411-bd1d-0c96b81fc2ee", "active_index_version": 2, "chunks": 35, "document_id": "2a72b9e0-aab3-4550-9bd9-875b58970100"}, {"active_build_id": "94a8fd3e-9ffc-4600-94c9-cca0a286b43a", "active_document_version_id": "0a2cadd4-aa5d-42f5-84ad-471f5194b3b6", "active_index_version": 1, "chunks": 2, "document_id": "e0bbf0d6-5b28-4abc-baa1-cc1c93269814"}] |
| `chat_model` | deeseek-v4.1-flash |
| `chat_base_url` | https://opencode.ai/inference/go |
| `chat_timeout_s` | 60.0 |
| `embedding_model_key` | BAAI/bge-small-zh-v1.5 |
| `embedding_dim` | 512 |
| `embedding_backend` | local |
| `retrieval_strategy` | baseline |
| `query_top_k` | 5 |
| `chat_top_k` | 6 |
| `reranker` | None |

## 5 诚实说明（读结论前先看这节）

- 样本数小（本轮 20 条，raw 20 行），P95 仅供参考：20 条样本的 95 分位基本等于「第二慢的那条」，不要当性能基线。
- repeats=1：min/max 与值相同，**波动无意义**，不能据此说「结果稳定」。
- 1 条无证据样本的 status 缺失，已退化为「回答含「没有检索到」」判定——权宜口径，done.status 落地后要改回。

## 6 产物

| 文件 | 内容 |
| --- | --- |
| `raw.jsonl` | 每样本每轮一行，含原始 results/citations/answer_text |
| `summary.json` | 上述指标 + per-category + repeats 波动（机器读） |
| `report.md` | 本文件 |
