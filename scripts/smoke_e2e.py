"""端到端烟测：用 httpx 打**真 API**，逐条核对修复计划 §0 的 A1–A9。

和 pytest 的分工：

- `tests/` 测的是单个函数/单个端点（夹具直接塞数据、用 FakeKbRepo 或 ASGI 传输），
  跑得快，但证明不了"真进程 + 真库 + 真 Worker 串起来能跑通"；
- 本脚本不 import 应用、不碰数据库会话，只走 HTTP。所以它能抓到夹具掩盖的问题：
  依赖没注入（kb_repo 为 None）、Worker 没起（job 永远 pending）、
  库指向错（上传 500）、embedding 后端与 KB 配置不匹配（检索 503）。

前提（缺一个就会失败，脚本会把缺哪个直接打出来）：

1. 已建表：`uv run python scripts/migrate.py`；
2. API 起着：`uv run uvicorn pyknora.api.main:app --port 8000`；
3. Worker 起着：`uv run python scripts/run_worker.py`（A4 要等 job 变 succeeded）；
4. **embedding 后端与 KB 配置要一致**：默认 `PYKNORA_EMBEDDING_BACKEND=local`
   会去下载 `BAAI/bge-small-zh-v1.5`。离线或不想等下载时，用
   `PYKNORA_EMBEDDING_BACKEND=hashing` 起 API/Worker/本脚本三者，
   并保持 `--model-key test/hashing --dim 512`（默认值就是这个组合）。

用法::

    uv run python scripts/smoke_e2e.py
    uv run python scripts/smoke_e2e.py --base-url http://127.0.0.1:8010 --keep

退出码：0 = 全过（允许 SKIP）；1 = 有 FAIL；2 = 连不上 API。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from typing import Any

import httpx

# 一份带锚点的正文：既能让关键词检索命中（"蓝鲸"），又足够长以切出多个 chunk。
SAMPLE_TEXT = (
    "蓝鲸锚点：本段用于端到端烟测。\n"
    "第一段讲知识库的增量索引：同一份内容重复上传应当被去重，不产生新的版本。\n"
    "第二段讲权限隔离：跨工作区的检索必须返回空结果，绝不能靠客户端过滤。\n"
    "第三段讲发布事务：旧索引退休、新索引激活、文档三指针切换必须在同一个事务里完成。\n"
    "第四段讲来源追踪：每个版本都要记下创建者与摄入口，否则审计链条是断的。\n"
)

PASSWORD = "smoke-pass-123"

_results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append((name, ok, detail))
    mark = "OK  " if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""), flush=True)
    return ok


def skip(name: str, detail: str) -> None:
    _results.append((name, True, detail))
    print(f"[SKIP] {name} — {detail}", flush=True)


def error_code(response: httpx.Response) -> str:
    """取统一错误体里的 code；取不到就回一个空串，别让烟测自己抛异常。"""
    try:
        body = response.json()
    except (json.JSONDecodeError, ValueError):
        return ""
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("code", ""))
    return ""


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@dataclass
class Identity:
    username: str
    token: str
    principal_id: str
    workspace_id: str


def register_or_login(client: httpx.Client, username: str) -> Identity:
    """注册；同名已存在则登录（烟测可重复跑，不该每次都清库）。"""
    payload = {
        "username": username,
        "email": f"{username}@smoke.local",
        "password": PASSWORD,
        "display_name": "烟测用户",
    }
    response = client.post("/api/v1/auth/register", json=payload)
    if response.status_code == 409:
        response = client.post(
            "/api/v1/auth/login", json={"username": username, "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
    else:
        assert response.status_code == 201, response.text
    body = response.json()
    return Identity(
        username=username,
        token=body["access_token"],
        principal_id=body["principal_id"],
        workspace_id=body["workspace_id"],
    )


def wait_job(client: httpx.Client, token: str, job_id: str, timeout: float) -> dict[str, Any]:
    """轮询到终态；等到超时也返回当前状态，由调用方判定失败原因。"""
    deadline = time.monotonic() + timeout
    detail: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}", headers=auth(token))
        if response.status_code != 200:
            return {
                "status": f"http {response.status_code}",
                "error_code": error_code(response),
                "terminal": True,
            }
        detail = response.json()
        if detail.get("terminal"):
            return detail
        time.sleep(0.5)
    return detail


def parse_sse(text: str) -> list[tuple[str, str]]:
    """把 SSE 正文拆成 (event, data) 序列；只认可解析的 data 行。"""
    events: list[tuple[str, str]] = []
    event = "message"
    datas: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if not line:
            if datas:
                events.append((event, "\n".join(datas)))
            event, datas = "message", []
            continue
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            datas.append(line[len("data:") :].lstrip())
    if datas:
        events.append((event, "\n".join(datas)))
    return events


def main() -> int:
    parser = argparse.ArgumentParser(description="PyKnora 端到端烟测（真 HTTP）")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--job-timeout", type=float, default=90.0, help="等 job 终态的秒数")
    parser.add_argument("--model-key", default="test/hashing", help="必须与 API 的 embedder 一致")
    parser.add_argument("--dim", type=int, default=512)
    parser.add_argument("--chunk-size", type=int, default=512)
    parser.add_argument("--chunk-overlap", type=int, default=64)
    parser.add_argument("--keep", action="store_true", help="跑完不清理（留现场排查）")
    args = parser.parse_args()

    stamp = str(int(time.time()))
    timeout = httpx.Timeout(30.0, read=120.0)  # chat 是流式，read 给宽一点
    with httpx.Client(base_url=args.base_url, timeout=timeout) as client:
        # ---- A1 健康检查 -------------------------------------------------
        try:
            health = client.get("/health")
        except httpx.HTTPError as exc:
            print(f"[FAIL] A1 /health 连不上 {args.base_url}：{exc}", flush=True)
            print("       先起 API：uv run uvicorn pyknora.api.main:app --port 8000", flush=True)
            return 2
        ok = health.status_code == 200 and health.json().get("status") == "ok"
        check("A1 /health 返回 status=ok", ok, health.text[:200])
        if not ok:
            return 1
        health_body = health.json()
        embedder_key = health_body.get("embedder")
        check(
            "A1 embedder 已注入（来自 lifespan）",
            bool(embedder_key),
            f"embedder={embedder_key!r}",
        )
        if embedder_key and args.model_key != str(embedder_key):
            print(
                f"[WARN] /health 的 embedder={embedder_key!r} 与 "
                f"--model-key={args.model_key!r} 不一致，"
                "后面建库会成功但检索会因模型/维度不匹配报 INDEX_INCOMPLETE",
                flush=True,
            )

        # ---- A2 注册 + 登录 ----------------------------------------------
        first = register_or_login(client, f"smokea{stamp}")
        check(
            "A2 注册返回 token/principal/workspace",
            bool(first.token and first.principal_id and first.workspace_id),
            f"principal={first.principal_id} workspace={first.workspace_id}",
        )
        login = client.post(
            "/api/v1/auth/login", json={"username": first.username, "password": PASSWORD}
        )
        check("A2 登录可用", login.status_code == 200, f"{login.status_code}")
        bad = client.post(
            "/api/v1/auth/login", json={"username": first.username, "password": "wrong-pass-1"}
        )
        check(
            "A2 错密码只回 401 invalid credentials（不区分用户是否存在）",
            bad.status_code == 401 and error_code(bad) == "UNAUTHENTICATED",
            f"{bad.status_code} {error_code(bad)}",
        )

        # ---- A3 建知识库（含重名 409）------------------------------------
        kb_payload = {
            "name": f"烟测知识库 {stamp}",
            "embedding_model_key": args.model_key,
            "embedding_dim": args.dim,
            "chunk_size": args.chunk_size,
            "chunk_overlap": args.chunk_overlap,
            "normalize": True,
        }
        created = client.post("/api/v1/knowledge-bases", json=kb_payload, headers=auth(first.token))
        check(
            "A3 建库返回 201 且带 profile",
            created.status_code == 201 and created.json().get("profile", {}).get("embedding_dim"),
            f"{created.status_code} {created.text[:160]}",
        )
        if created.status_code != 201:
            return _summary()
        kb = created.json()
        kb_id = kb["kb_id"]
        duplicate = client.post(
            "/api/v1/knowledge-bases", json=kb_payload, headers=auth(first.token)
        )
        check(
            "A3 同区重名返回 409 CONFLICT",
            duplicate.status_code == 409 and error_code(duplicate) == "CONFLICT",
            f"{duplicate.status_code} {error_code(duplicate)}",
        )

        # ---- A4 上传 → job succeeded ------------------------------------
        files = {"file": ("smoke.txt", SAMPLE_TEXT.encode("utf-8"), "text/plain")}
        uploaded = client.post(
            f"/api/v1/knowledge-bases/{kb_id}/documents",
            files=files,
            headers=auth(first.token),
        )
        check(
            "A4 上传返回 202 + job_id",
            uploaded.status_code == 202 and bool(uploaded.json().get("job_id")),
            f"{uploaded.status_code} {uploaded.text[:200]}",
        )
        if uploaded.status_code != 202:
            return _summary()
        accepted = uploaded.json()
        document_id = accepted["document_id"]
        job = wait_job(client, first.token, accepted["job_id"], args.job_timeout)
        not_terminal = (
            ""
            if job.get("terminal")
            else f"（等了 {args.job_timeout:.0f}s 仍未终态：Worker 没起？）"
        )
        check(
            "A4 job 到达 succeeded（Worker 真的在跑）",
            job.get("status") == "succeeded",
            f"status={job.get('status')!r} error={job.get('error_code')!r}{not_terminal}",
        )

        # ---- A5 chunk 边界 ----------------------------------------------
        chunks = client.get(f"/api/v1/documents/{document_id}/chunks", headers=auth(first.token))
        body = chunks.json() if chunks.status_code == 200 else {}
        items = body.get("items") or []
        boundaries_ok = all(
            isinstance(item.get("char_start"), int)
            and isinstance(item.get("char_end"), int)
            and item["char_start"] < item["char_end"]
            for item in items
        )
        check(
            "A5 chunks 非空且每条 char_start < char_end",
            chunks.status_code == 200 and bool(items) and boundaries_ok,
            f"{chunks.status_code} total={body.get('total')} items={len(items)}",
        )

        # ---- A6 检索命中 -------------------------------------------------
        searched = client.post(
            f"/api/v1/knowledge-bases/{kb_id}/query",
            json={"query": "蓝鲸锚点", "top_k": 3},
            headers=auth(first.token),
        )
        hits = (searched.json().get("results") if searched.status_code == 200 else None) or []
        check(
            "A6 query 命中刚上传的内容",
            searched.status_code == 200 and bool(hits),
            f"{searched.status_code} hits={len(hits)}"
            + ("" if hits else f" {searched.text[:160]}"),
        )

        # ---- A7 流式问答 -------------------------------------------------
        try:
            chat = client.post(
                f"/api/v1/knowledge-bases/{kb_id}/chat",
                json={"query": "蓝鲸锚点讲了什么？", "top_k": 3},
                headers=auth(first.token),
            )
        except httpx.HTTPError as exc:
            # 流式端点最典型的坏法就是这里：服务端在发完响应头之后抛异常，
            # 客户端只看到连接被掐断，连错误体都拿不到。
            check(
                "A7 SSE 建流成功（连接未被掐断）",
                False,
                f"{type(exc).__name__}: {exc}（多半是流已开始后服务端抛异常）",
            )
            chat = None
        not_configured = (
            chat is not None
            and chat.status_code == 503
            and error_code(chat) == "DEPENDENCY_UNAVAILABLE"
        )
        if not_configured:
            skip(
                "A7 SSE 顺序 retrieval→token*→done",
                f"未配 chat 模型：{chat.text[:120]}（换个带 key 的环境再验）",
            )
        elif chat is not None and chat.status_code != 200:
            check("A7 SSE 建流成功", False, f"{chat.status_code} {chat.text[:200]}")
        elif chat is not None:
            events = parse_sse(chat.text)
            names = [name for name, _ in events]
            first_is_retrieval = bool(names) and names[0] == "retrieval"
            done_last = bool(names) and names[-1] == "done"
            has_token = "token" in names
            errored = [data for name, data in events if name == "error"]
            check(
                "A7 SSE 顺序 retrieval→token*→done",
                first_is_retrieval and has_token and done_last and not errored,
                f"events={names[:6]}{'…' if len(names) > 6 else ''} ({len(names)} 个)"
                + (f" error={errored[0][:120]}" if errored else ""),
            )
            if done_last:
                try:
                    json.loads(events[-1][1])
                except json.JSONDecodeError:
                    check("A7 done 事件是合法 JSON", False, events[-1][1][:120])

        # ---- A8 来源追踪 -------------------------------------------------
        lineage = client.get(f"/api/v1/documents/{document_id}/lineage", headers=auth(first.token))
        lineage_body = lineage.json() if lineage.status_code == 200 else {}
        versions = lineage_body.get("versions") or []
        contributors = lineage_body.get("contributors") or []
        creator_ok = all(
            version.get("created_by_principal_id") == first.principal_id for version in versions
        )
        source_ok = all(version.get("ingestion_source") == "upload" for version in versions)
        check(
            "A8 lineage 有版本、创建者是本人、来源=upload",
            lineage.status_code == 200
            and bool(versions)
            and first.principal_id in contributors
            and creator_ok
            and source_ok,
            f"{lineage.status_code} versions={len(versions)} contributors={len(contributors)}",
        )

        # ---- A9 跨区隔离 -------------------------------------------------
        second = register_or_login(client, f"smokeb{stamp}")
        cross = client.post(
            f"/api/v1/knowledge-bases/{kb_id}/query",
            json={"query": "蓝鲸锚点", "top_k": 3},
            headers=auth(second.token),
        )
        cross_hits = (cross.json().get("results") if cross.status_code == 200 else None) or []
        cross_detail = client.get(f"/api/v1/documents/{document_id}", headers=auth(second.token))
        check(
            "A9 他人知识库：检索不被泄漏（404 或空结果）",
            (cross.status_code == 404 and error_code(cross) == "RESOURCE_NOT_FOUND")
            or (cross.status_code == 200 and not cross_hits),
            f"query={cross.status_code}/{len(cross_hits)} 条，doc={cross_detail.status_code}",
        )

        # ---- 清理（默认做，--keep 留现场）--------------------------------
        if not args.keep:
            current = client.get(f"/api/v1/knowledge-bases/{kb_id}", headers=auth(first.token))
            if current.status_code == 200:
                version = current.json().get("row_version", 1)
                removed = client.request(
                    "DELETE",
                    f"/api/v1/knowledge-bases/{kb_id}",
                    headers={**auth(first.token), "If-Match": str(version)},
                )
                print(f"       清理：DELETE kb -> {removed.status_code}", flush=True)

    return _summary()


def _summary() -> int:
    failed = [name for name, ok, _ in _results if not ok]
    passed = sum(1 for _, ok, _ in _results if ok)
    print(f"\n合计 {len(_results)} 项：通过 {passed}，失败 {len(failed)}", flush=True)
    if failed:
        print("失败项：" + "；".join(failed), flush=True)
        return 1
    print("烟测通过。", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
