# eval/baselines/ · 门禁基线快照

这里是 `eval/gate.py` 的比对基准：**入库**（`eval/results/` 相反，仍不入库）。
每个子目录是一轮评测的「三件套」原样拷贝：`summary.json` + `raw.jsonl` + `report.md`，
三者缺一，`gate.py` 直接判 `UNKNOWN` / exit 2。

## 规矩：只增不改

- 已有快照**一个字都不改**。要新基线就新建目录（`baseline-<YYYYMMDD>`），不要覆盖旧的。
- 快照必须与 `eval/results/<run>/` 逐字节一致：拷完用
  `Get-FileHash eval\results\<run>\*` 与这里的同名文件比对 sha256（2026-11-02 已比对 `same`）。
- 目录名与 `eval/results/` 里那一轮保持一致，便于回溯「这个数字出自哪次运行」。
- 指标只能引自快照，不许手写；引用时带齐 commit / 哪份配置 / 样本量（见 `eval/README.md` §4）。

## 现有快照

| 目录 | 来源 | 用途 | 门禁自比 |
| --- | --- | --- | --- |
| `baseline-20261030/` | `eval/results/baseline-20261030/`（`retrieval_strategy=baseline`） | **门禁默认基线**（20 条旧集） | **exit 0**（2026-11-09 实测） |
| `candidate-20261030/` | `eval/results/candidate-20261030/`（`retrieval_strategy=candidate`） | 已知的真实退化样本：`citation_accuracy` 0.3556→0.3182、`abstain_accuracy` 0.6667→0.3333、逐例 `noev-03` 由 `ABSTAINED` 变 `COMPLETED` | — |
| `baseline-20261108-r3/` | 真实 bge + 真实 LLM 的**修前**干净基线（30 条 / 8 类，`repeats=3`，90 行 raw） | `docs/Day03/09` 总表的「修前」列锚点 | **exit 4**（`null_ratio=1.0`） |
| `baseline-20261108-r3j/` | 同库同语料的**修后**终点（判定层 / 引用粒度 / 拒答状态 / 文档有效期四处改动） | **当前指标证据基线**：`docs/Day03/09` 与面试材料引用的数字一律出自这里 | **exit 4**（`null_ratio=1.0`） |

### `r3` / `r3j` 是指标证据快照，**不是门禁可用基线**

2026-11-09 实测：`gate.py --baseline …-r3j --candidate …-r3j` → **exit 4**
（`quality FAIL / safety PASS / perf REPORT_ONLY`），阻断原因是 `null_ratio=1.0000 > 0.2`——
30 条样本的 `fact`/`citation`/`security` 三项人工复核 verdict 全为 `null`（各 90 条），一条都没回填。
按本文「自比必须 exit 0」的规矩，**这两轮不能当门禁基准**；门禁默认基线仍是 `baseline-20261030`。
解冻要用 `eval/review.py` 逐条回填，且 **`auto:` 初判不等于人工签字**。
安全项单独看是过的（`deny_leak=false`，36 条 deny 组记录无泄漏迹象）。
完整判决与溯源说明见 `docs/Day03/09-评测指标提升记录-20261109.md` §0。

**入库口径（本轮冻结）**：只入 `r3` 与 `r3j` 两个端点——它们是 `docs/Day03/09` 总表两列的唯一证据。
`r3b`–`r3i`、`r3k`、`baseline-20261108`、`candidate-20261108-r3` 共 11 个中间轮**留在本地磁盘、不入库**
（`eval/results/` 同样不入库），各轮含义已逐行记在 `docs/Day03/09` §6；其中 `r3k` 是「带引用就不算拒答」
实验，**指标退化、未采用**。不入库不等于删除，需要时按 §6 的含义就地取用。

**溯源弱点（明写，不掩盖）**：`r3`/`r3j` 的 `summary.json` 里 `app_commit` 都是 `db90d79`，
而四处修复落盘的提交是 `7aa24f8`（`db90d79` 是它的祖先，已用 `git merge-base --is-ancestor` 验证）——
跑评测时修复代码还在工作区未提交，且 `summary.json` **没有 `app_dirty` 字段**，
所以快照自身不能证明"跑的是哪份工作区代码"。轮次 ↔ 改动的对应关系只能靠 `docs/Day03/09` §2/§6。

**sha256 锚点**（源运行目录已不在 `eval/results/`，无法再做逐字节回比；以此锚点供今后核对快照未被改动）：

| 目录 | 文件 | 字节 | sha256 |
| --- | --- | --- | --- |
| `baseline-20261108-r3/` | `summary.json` | 30693 | `89867ef02c8cf7e1a4a61f288a1a04fc472cd97d03d63654ea05046905123d21` |
| | `raw.jsonl` | 765234 | `2cfc7757e8559c57f9f9caaa12462229fb0f58ecbd3ab2e9a985349fb7f48024` |
| | `report.md` | 10884 | `77cc93356d86b953867cb412ee12fbf9a0be1de7354ecdf58d0cb3d0e416d019` |
| `baseline-20261108-r3j/` | `summary.json` | 20716 | `81abe3a5c12965292c2ab370b017a3ec62d5310509da28a579e251dd4b295338` |
| | `raw.jsonl` | 548121 | `3493be1e566197d18e5bb3ad3869741103db73886d8038730cca4604218e2e17` |
| | `report.md` | 7676 | `f27826c930d4a4e6f393eb0aa05cfee1cac294781736f7b3498bf72e9991d693` |

三件套完整性已核：两个目录各 3 个文件、`raw.jsonl` 各 **90 行**（30 条 × `repeats=3`）。

**换行口径（2026-11-09 实测后加的护栏）**：`run_eval.py` 在 Windows 上以文本模式写产物，
落盘就是 **CRLF**；本机 `core.autocrlf=true` 会在 `git add` 时把它规范成 LF，于是
**committed blob 与源产物不再逐字节一致**，上面的 sha256 锚点在 `autocrlf=false` 的机器上也会失效。
因此 `.gitattributes` 追加了两条 `-text`（只覆盖 `baseline-20261108-r3/**` 与 `baseline-20261108-r3j/**`），
入库时不做任何换行转换。已实测：`git show :<path>` 取出的暂存 blob 与磁盘文件**逐字节相同**，
六个文件的 sha256 与上表锚点**全部 MATCH**。

**顺带查出的历史偏差（不改旧快照）**：`baseline-20261030/` 与 `candidate-20261030/` 的 blob
早已被规范成 LF（实测 `git show HEAD:eval/baselines/baseline-20261030/raw.jsonl` 的 CRLF 数为 **0**、
LF 数 20，而磁盘同名文件 CRLF 数 20），所以本文件开头那句「2026-11-02 已比对 `same`」比的是
**当时两份磁盘文件**，不是 blob；在 Linux/CI 上检出这两轮会得到 LF 字节，与 Windows 磁盘上的 CRLF 哈希不同。
给它们补 `-text` 会改变检出字节、推翻那次比对记录，故**只登记、不动旧快照**（只增不改）。

### 1030 两轮的可比性限制

两轮的 `app_commit` 都是 `4e16d652cea0f730244571dd92c16a1a112f0be4`，`repeats=1`，
20 条样本（扩集前的评测集；现集已扩到 30 条，这 10 条新样本没有本目录里的历史基线）
—— 所以 `min=max`，波动无意义，P95 只作参照。
**1030 与 1108 各轮不可直接相减**：样本量 20 vs 30、gold 已按新库重绑、向量与语料都换过。
`r3` → `r3j` 才是同库同语料同评测集的对照。

## 新增一轮基线

```powershell
# 1) 跑完一轮，产物在 eval/results/<run>/
uv run --frozen python eval/run_eval.py --config eval/baseline_config.yaml --repeats 1 --out eval/results/base-20261110

# 2) 拷三件套进来（目录名与 <run> 一致）
$dst = 'eval/baselines/baseline-20261110'
New-Item -ItemType Directory -Force -Path $dst | Out-Null
Copy-Item 'eval/results/base-20261110/summary.json','eval/results/base-20261110/raw.jsonl','eval/results/base-20261110/report.md' $dst

# 3) 自比必须 exit 0（否则快照本身就是坏的）
uv run --frozen python eval/gate.py --baseline $dst --candidate $dst
```

## 阈值放哪

阈值不属于快照：`gate.py` 从**基线 summary** 的 `thresholds` 段读（可选）。
`baseline-20261030` / `candidate-20261030` 两轮**没有**该段，因此 `max_null_ratio` 走计划 P4 的 0.20 兜底、
`max_metric_drop` 走 0.0，`gate.py` 会在 reasons 里写明「兜底来源」。
`baseline-20261108-r3` / `-r3j` 两轮**已带**该段且值相同：`{"max_null_ratio": 0.2, "max_metric_drop": 0.0}`
——所以它们的 exit 4 不是兜底导致的，是 `null_ratio=1.0` 实打实超了 0.2。
要改阈值，就在新快照的 `summary.json` 里加 `"thresholds": {…}` —— 但那是**新目录**，
不是改旧快照；**尤其不许为了让某一轮过门禁而放宽它的 `max_null_ratio`**，那等于把"没复核"写成"复核过了"。
