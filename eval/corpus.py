"""Day03 评测语料落位与指纹校验（T0.2）。

三件事必须在动笔前说清楚，否则代码里的"复制 + 算哈希"看着像多余动作：

1) **为什么复制而不是引用原路径**：`基本财务管理制度.pdf` 的原件在桌面课程资料里，
   `员工手册v1/v2.docx` 的原件在 `.tmp_ref/` 根下——这两处都不受本仓库控制（桌面文件会被
   人为替换、`.tmp_ref/` 会被各种脚本重写）。评测要的是"同一批字节、同一套 gold 定位"，
   一旦原路径漂移，30 条样本的 `gold_chunks` 全部失效却没人会发现。复制到固定的
   `.tmp_ref/eval_corpus/` 后，评测只依赖这一份拷贝，原件怎么变都不影响复现。

2) **为什么要 sha256**：语料是基线实验的自变量。同一份 `eval_set_v0.1.jsonl` 配两份不同的
   `员工手册v2.docx`，跑出来的 Recall@5 不可比，但报告里两个数字长得一模一样。sha256 是
   "语料指纹"——把自变量钉死，换机器/换人重放时能立刻回答"还是不是那一份"。
   （`chunk_ordinal` + `content_sha1_8` 定位的是切片，sha256 定位的是切片之前的原始字节，
   两层指纹各管一段。）

3) **为什么虚构文档用 `.md`**：上传白名单是 PDF/DOCX/TXT/MD/HTML（`services/ingest/parser.py:19`），
   `.md` 在其中。为两篇自造语料引入 docx 生成链路（python-docx 写文件 + 段落样式 + 每次
   生成的 zip 时间戳都会变）不划算：`.md` 的字节完全由本文件的字符串常量决定，同一份代码
   在任何机器上生成同一串 sha256，而这正是第 2 条想要的。

用法（默认行为就是 `--prepare`）：

    uv run python eval/corpus.py                    # 幂等落位 5 份文件 + 写清单
    uv run python eval/corpus.py --verify-only      # 按清单重算，不一致 exit 1
    uv run python eval/corpus.py --list             # 只打印清单

语料本身不入库（`.gitignore` 的 `.tmp_ref/` 已覆盖），入股的只有 `eval/corpus_manifest.json`。
换机器要重新放文件后跑一次 `--prepare`；`--verify-only` 失败就报错，绝不静默降级。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = REPO_ROOT / ".tmp_ref" / "eval_corpus"
MANIFEST_PATH = REPO_ROOT / "eval" / "corpus_manifest.json"

# 桌面原件的位置不写死在代码里：换一台机器/换一个人，桌面路径必然不同，而换路径不该改代码。
# 没设这个环境变量时退回下面这个默认值（本机实测路径）。
PDF_SOURCE_ENV = "PYKNORA_EVAL_PDF_SRC"
DEFAULT_PDF_SOURCE = Path(
    r"C:\Users\Administrator\Desktop\课程\agent教程\项目一\基本财务管理制度.pdf"
)

SOURCE_NOTE = (
    "基本财务管理制度.pdf 复制自课程资料桌面原件；员工手册v1/v2.docx 复制自仓库 .tmp_ref/ 根下；"
    "财务内部预算.md 与 团建与年会通知.md 由 eval/corpus.py 生成。语料不入库（.gitignore 的 "
    ".tmp_ref/），换机器需重新放文件并重跑 --prepare。target=main 进 workspace A 的评测 KB，"
    "target=forbidden 只上传到 workspace B，供 deny 样本证明「越权被拦」而非「库不存在」。"
)

# 自造的越权语料：金额全部经实测确认不在另外三份真实文档里出现（PDF 通篇无"万元"级数字，
# 两本手册的金额只有 50/1000/1300/1800/2000 元这几档），避免 gold 回填时把越权库的数字
# 错认成 A 的证据。
_BUDGET_MD = """# 财务内部预算（2026 年度）

> 本文件仅限财务负责人及以上查阅。未经财务负责人书面授权，不得转发、截图或对外引用。

## 一、总体口径

2026 年度预算按「部门申报 → 财务复核 → 总经理办公会审定」三步定稿。年中只保留一次调整窗口，
为每年 8 月 10 日至 8 月 20 日；窗口外不再受理部门提交的预算变更申请。

## 二、分部门预算（节选）

| 部门 | 2026 年度预算总额 | 其中差旅预算 |
| --- | --- | --- |
| 研发中心 | 1200 万元 | 60 万元 |
| 市场部 | 260 万元 | 45 万元 |

研发中心 2026 年度预算总额 1200 万元，含设备采购与云资源两部分，其中云资源按季度预付。

## 三、专项预算

机房扩容专项预算 78 万元，须在 2026 年 9 月 30 日前执行完毕，逾期未执行部分收回总盘子。

## 四、审批口径

单笔超 50 万元的预算调整，除部门负责人签批外，还须财务负责人审批后方可执行；跨部门调剂
由财务负责人汇总后统一报总经理办公会。
"""

# 干扰文档：只写年会与团建本身。刻意不出现"报销/差旅/年假/预算"等与被测问题同主题的词，
# 否则它就不是"无关内容"，而是给检索送分的近邻；同样不写任何金额，避免与 fact 类 gold 撞数字。
_NOTICE_MD = """# 关于 2027 年迎新年会与春季团建的通知

各位同事：

为感谢大家一年来的付出，公司定于 2027 年 1 月 16 日（周六）举办迎新年会，现将有关事项通知如下。

## 一、时间与地点

- 年会时间：2027 年 1 月 16 日 17:30 入场，18:00 正式开始。
- 年会地点：上海市浦东新区张江路 88 号海兰大厦 3 楼多功能厅。
- 交通：地铁 2 号线张江高科站 4 号口步行约 8 分钟；大厦地下车库当晚免费。

## 二、报名

- 报名截止时间：2026 年 12 月 31 日 17:00，逾期不再补录。
- 报名方式：各部门行政对接人汇总名单后发至行政部邮箱，名单需注明是否携带家属。
- 座位按报名先后编排，同一部门尽量相邻。

## 三、节目征集

- 征集截止时间：2026 年 12 月 20 日 18:00。
- 形式不限：歌舞、小品、乐器、魔术、脱口秀均可，单个节目时长不超过 6 分钟。
- 报名时请附节目名称、参演人数与所需道具，行政部统一安排彩排。

## 四、春季团建

春季团建安排在 2027 年 4 月中旬，以部门为单位自行组织，具体日期由各部门与行政部协商后确定。
团建当天的用车与场地由行政部统一预订，部门只需在活动前一周提交人数。

请各部门于本周内确定对接人并回复本邮件。

行政部
2026 年 12 月 1 日
"""


@dataclass(frozen=True)
class FileSpec:
    """一份语料的落位方式。source 与 text 二选一：前者复制，后者现造。"""

    name: str
    kind: str  # pdf | docx | md
    target: str  # main | forbidden
    description: str
    source: Path | None = None
    text: str | None = None


def _build_specs() -> tuple[FileSpec, ...]:
    pdf_source = Path(os.environ.get(PDF_SOURCE_ENV, DEFAULT_PDF_SOURCE))
    return (
        FileSpec(
            name="基本财务管理制度.pdf",
            kind="pdf",
            target="main",
            description="真实制度文档，40 页；条款号与审批流程是 exact_term 类样本的出处",
            source=pdf_source,
        ),
        FileSpec(
            name="员工手册v1.docx",
            kind="docx",
            target="main",
            description="真实文档首版；与 v2 同名重传，用来把 active_index_version 顶到 2",
            source=REPO_ROOT / ".tmp_ref" / "员工手册v1.docx",
        ),
        FileSpec(
            name="员工手册v2.docx",
            kind="docx",
            target="main",
            description="真实文档第二版；重传后 v1 的切片应被 retire，只有 v2 现行",
            source=REPO_ROOT / ".tmp_ref" / "员工手册v2.docx",
        ),
        FileSpec(
            name="团建与年会通知.md",
            kind="md",
            target="main",
            description="无关干扰文档；制造 no_evidence 与 top5 噪声，不得命中被测问题",
            text=_NOTICE_MD,
        ),
        FileSpec(
            name="财务内部预算.md",
            kind="md",
            target="forbidden",
            description="越权语料，仅上传到 workspace B；A 的令牌问它必须 404 且无片段",
            text=_BUDGET_MD,
        ),
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_if_changed(path: Path, data: bytes) -> bool:
    """内容相同就不动文件。

    不是为了省 IO，而是为了让 `--prepare` 真正幂等：Windows 上重写会让 mtime 变，
    文件监视类工具和"看修改时间判断有没有跑过"的习惯都会被假更新误导。返回是否写了。
    """
    if path.is_file() and path.read_bytes() == data:
        return False
    path.write_bytes(data)
    return True


def _materialize(spec: FileSpec) -> tuple[str, str]:
    """落位一份语料，返回 (动作, 失败原因)。动作只有 复制/生成/跳过/沿用 四种。"""
    dest = CORPUS_DIR / spec.name
    if spec.text is not None:
        # newline="" 等价于"不转换换行"：写死 \n，否则 Windows 会写成 CRLF，
        # 同一份代码在两台机器上算出两个 sha256，指纹就失效了。
        data = spec.text.encode("utf-8")
        return ("生成" if _write_if_changed(dest, data) else "跳过", "")

    assert spec.source is not None
    if spec.source.is_file():
        if dest.is_file() and dest.read_bytes() == spec.source.read_bytes():
            return ("跳过", "")
        shutil.copyfile(spec.source, dest)
        return ("复制", "")

    if dest.is_file():
        # 原件不在了但拷贝还在：评测的可复现锚是清单里的 sha256，不是原件路径，
        # 所以沿用已有拷贝是安全降级；但要说出来，不能让人以为刚复制过。
        return ("沿用", f"原件不存在，沿用已有拷贝：{spec.source}")

    return ("缺失", f"原件与目标都不存在：{spec.source}（可用环境变量 {PDF_SOURCE_ENV} 指定）")


def _display_width(text: str) -> int:
    """中文 Windows 控制台把 CJK 按 2 列宽渲染，用 len() 对齐会整体右移。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


def _print_table(rows: list[tuple[str, str, str, str, str]]) -> None:
    """中文表头 + ASCII 竖线：制表符（│─┌）与 emoji 在 cp936 控制台会直接抛
    UnicodeEncodeError，而 `|` 和 `-` 在任何编码下都能落地。"""
    headers = ("文件名", "字节", "sha256 前 12 位", "kind", "target")
    table = [headers, *rows]
    widths = [max(_display_width(row[i]) for row in table) for i in range(len(headers))]
    print("  ".join(_pad(cell, widths[i]) for i, cell in enumerate(headers)))
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        print("  ".join(_pad(cell, widths[i]) for i, cell in enumerate(row)))


def _table_row(
    name: str, size: int | None, digest: str, kind: str, target: str
) -> tuple[str, str, str, str, str]:
    return (
        name,
        "-" if size is None else str(size),
        digest[:12] if digest else "-",
        kind,
        target,
    )


def _build_manifest(specs: tuple[FileSpec, ...]) -> dict:
    files = []
    for spec in specs:
        data = (CORPUS_DIR / spec.name).read_bytes()
        files.append(
            {
                "name": spec.name,
                # 相对仓库根的 posix 路径：清单要能被别人 checkout 后直接读，不能带 E:\\ 前缀。
                "path": (CORPUS_DIR / spec.name).relative_to(REPO_ROOT).as_posix(),
                "bytes": len(data),
                "sha256": _sha256(data),
                "kind": spec.kind,
                "target": spec.target,
                "description": spec.description,
            }
        )
    return {"generated_at": "", "source_note": SOURCE_NOTE, "files": files}


def _write_manifest(payload: dict) -> str:
    """内容没变就不重写 generated_at。

    否则每次 `--prepare` 都在 `git status` 里制造一行无意义的 diff，评审时要多花时间确认
    "清单变了"其实什么都没变。返回给人看的动作说明。
    """
    if MANIFEST_PATH.is_file():
        try:
            old = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            old = None
        if isinstance(old, dict) and old.get("files") == payload["files"]:
            if old.get("source_note") == payload["source_note"]:
                return f"清单无变化，保留 generated_at={old.get('generated_at')}"
            payload["generated_at"] = old.get("generated_at") or ""
        else:
            payload["generated_at"] = ""
    if not payload["generated_at"]:
        payload["generated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")

    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    MANIFEST_PATH.write_text(text, encoding="utf-8", newline="\n")
    return "已重写清单"


def prepare() -> int:
    specs = _build_specs()
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)

    actions: dict[str, str] = {}
    warnings: list[str] = []
    for spec in specs:
        action, note = _materialize(spec)
        actions[spec.name] = action
        if note:
            warnings.append(f"{spec.name}：{note}")
        if action == "缺失":
            print(f"[失败] {spec.name}：{note}", file=sys.stderr)
            print("先放好原件（或用环境变量指定），再重跑 --prepare。", file=sys.stderr)
            return 1

    payload = _build_manifest(specs)
    rows = [
        _table_row(item["name"], item["bytes"], item["sha256"], item["kind"], item["target"])
        for item in payload["files"]
    ]
    print("语料目录：" + CORPUS_DIR.relative_to(REPO_ROOT).as_posix())
    _print_table(rows)
    print()
    for item in payload["files"]:
        print(f"  {actions[item['name']]}  {item['name']}")
    for line in warnings:
        print(f"  [注意] {line}")
    print("  " + _write_manifest(payload))
    print("  清单：" + MANIFEST_PATH.relative_to(REPO_ROOT).as_posix())
    return 0


_REQUIRED_KEYS = ("name", "path", "bytes", "sha256", "kind", "target", "description")


def verify() -> int:
    if not MANIFEST_PATH.is_file():
        print(f"[失败] 找不到清单 {MANIFEST_PATH}，先跑一次 --prepare。", file=sys.stderr)
        return 1
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[失败] 清单不是合法 JSON：{exc}", file=sys.stderr)
        return 1

    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, list) or not files:
        print("[失败] 清单缺少 files 数组或为空。", file=sys.stderr)
        return 1

    rows: list[tuple[str, str, str, str, str]] = []
    problems: list[str] = []
    for item in files:
        missing_keys = [key for key in _REQUIRED_KEYS if key not in item]
        if missing_keys:
            problems.append(f"{item.get('name', '<无名>')}：清单缺字段 {missing_keys}")
            continue
        path = REPO_ROOT / item["path"]
        if not path.is_file():
            rows.append(_table_row(item["name"], None, "", item["kind"], item["target"]))
            problems.append(f"{item['name']}：文件不存在（{item['path']}）")
            continue
        data = path.read_bytes()
        digest = _sha256(data)
        rows.append(_table_row(item["name"], len(data), digest, item["kind"], item["target"]))
        if digest != item["sha256"]:
            problems.append(
                f"{item['name']}：sha256 不一致（清单 {item['sha256'][:12]} / 实算 {digest[:12]}）"
            )
        elif len(data) != item["bytes"]:
            problems.append(
                f"{item['name']}：字节数不一致（清单 {item['bytes']} / 实算 {len(data)}）"
            )

    print("语料目录：" + CORPUS_DIR.relative_to(REPO_ROOT).as_posix())
    print("清单生成时间：" + str(manifest.get("generated_at")))
    print()
    _print_table(rows)

    listed = {item.get("name") for item in files if isinstance(item, dict)}
    if CORPUS_DIR.is_dir():
        extras = sorted(
            path.name for path in CORPUS_DIR.iterdir() if path.is_file() and path.name not in listed
        )
        if extras:
            print()
            print(f"[注意] 目录里有清单之外的文件（不判失败）：{extras}")

    expected = {spec.name for spec in _build_specs()}
    if expected != listed:
        print()
        print(
            "[注意] 清单与代码里的语料清单不是同一套："
            f"代码有而清单无 {sorted(expected - listed)}；"
            f"清单有而代码无 {sorted(listed - expected)}"
        )

    print()
    if problems:
        print(f"[失败] 语料与清单不一致：{len(problems)} 处", file=sys.stderr)
        for line in problems:
            print(f"  - {line}", file=sys.stderr)
        print("确认是不是有人动了语料；要接受新语料就重跑 --prepare 并同步 gold。", file=sys.stderr)
        return 1

    print(f"[通过] {len(files)}/{len(files)} 份语料与清单一致。")
    return 0


def list_manifest() -> int:
    if not MANIFEST_PATH.is_file():
        print(f"[失败] 找不到清单 {MANIFEST_PATH}，先跑一次 --prepare。", file=sys.stderr)
        return 1
    print(MANIFEST_PATH.read_text(encoding="utf-8"), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Day03 评测语料落位与指纹校验（不带参数时等价于 --prepare）"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare", action="store_true", help="幂等落位 5 份语料并写清单")
    mode.add_argument("--verify-only", action="store_true", help="按清单重算 sha256，不一致 exit 1")
    mode.add_argument("--list", dest="list_only", action="store_true", help="只打印清单")
    args = parser.parse_args(argv)

    if args.verify_only:
        return verify()
    if args.list_only:
        return list_manifest()
    return prepare()


if __name__ == "__main__":
    raise SystemExit(main())
