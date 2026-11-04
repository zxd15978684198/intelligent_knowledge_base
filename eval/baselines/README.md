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

| 目录 | 来源 | 用途 |
| --- | --- | --- |
| `baseline-20261030/` | `eval/results/baseline-20261030/`（`retrieval_strategy=baseline`） | 门禁默认基线 |
| `candidate-20261030/` | `eval/results/candidate-20261030/`（`retrieval_strategy=candidate`） | 已知的真实退化样本：`citation_accuracy` 0.3556→0.3182、`abstain_accuracy` 0.6667→0.3333、逐例 `noev-03` 由 `ABSTAINED` 变 `COMPLETED` |

两轮的 `app_commit` 都是 `4e16d652cea0f730244571dd92c16a1a112f0be4`，`repeats=1`，
20 条样本（扩集前的评测集；现集已扩到 30 条，这 10 条新样本没有本目录里的历史基线）
—— 所以 `min=max`，波动无意义，P95 只作参照。

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
当前两轮快照都没有该段，因此 `max_null_ratio` 走计划 P4 的 0.20 兜底、`max_metric_drop`
走 0.0，`gate.py` 会在 reasons 里写明「兜底来源」。要改阈值，就在新快照的 `summary.json`
里加 `"thresholds": {"max_null_ratio": 0.2, "max_metric_drop": 0.0}` —— 但那是**新目录**，
不是改旧快照。
