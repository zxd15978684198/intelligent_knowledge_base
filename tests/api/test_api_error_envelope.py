"""统一错误体：**所有**失败路径都必须是 `{"error":{...},"request_id":...}`。

## 为什么要专门一个文件钉这件事

在补这两类 handler 之前，错误体有两种形状：

- 业务错误（`AppError`）→ `{"error":{code,message,retryable,details},"request_id":...}`
- 框架错误（请求体校验 422、未知路由 404、方法不允许 405）→ `{"detail": ...}`

同一个客户端要写两套解析，而且第二种**不带 `request_id`**，报障时对不上服务端日志。
两种形状并存时，最容易被漏掉的是"用户看到的那一次错误恰好来自框架层"——
它通常出现在输入畸形、地址写错这类最需要排障信息的时刻。

这里逐条钉住：状态码、`error.code`、`request_id` 的成对出现、以及**不回显请求体**。
"""

from __future__ import annotations

import httpx

from tests.api.tokens import bearer

REGISTER_URL = "/api/v1/auth/register"


async def test_validation_error_uses_unified_body(client: httpx.AsyncClient) -> None:
    """422 走统一错误体：`error.code=INVALID_REQUEST`，并带 `request_id`。"""
    r = await client.post(REGISTER_URL, json={"username": "u1", "password": "short"})

    assert r.status_code == 422, r.text
    body = r.json()
    assert "detail" not in body, "框架默认形状漏出来了"
    assert body["error"]["code"] == "INVALID_REQUEST"
    assert body["error"]["retryable"] is False
    assert body["request_id"] == r.headers["x-request-id"]

    # 定位信息必须还在：只有码没有 loc，用户不知道哪个字段错了。
    errors = body["error"]["details"]["errors"]
    assert errors and all({"loc", "type", "msg"} <= set(item) for item in errors)
    assert any("password" in ".".join(item["loc"]) for item in errors)


async def test_validation_error_does_not_echo_secrets(client: httpx.AsyncClient) -> None:
    """错误体不能把请求体原文回显出去——注册接口的密码就在里面。

    pydantic 的 `exc.errors()` 每项都带 `input`，直接透传等于把用户密码写进
    响应体（以及任何记录响应体的日志/链路追踪）。
    """
    secret = "SuperSecret-Passw0rd-do-not-echo"
    r = await client.post(REGISTER_URL, json={"username": "u2", "password": "x", "extra": secret})

    assert r.status_code == 422, r.text
    assert secret not in r.text
    assert "input" not in r.text


async def test_unknown_route_is_404_with_unified_body(client: httpx.AsyncClient) -> None:
    """未知路由也走统一错误体（此前是 `{"detail":"Not Found"}`，没有 request_id）。"""
    r = await client.get("/api/v1/no-such-endpoint", headers=bearer())

    assert r.status_code == 404, r.text
    body = r.json()
    assert "detail" not in body
    assert body["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert body["request_id"] == r.headers["x-request-id"]


async def test_method_not_allowed_keeps_405_status(client: httpx.AsyncClient) -> None:
    """405 保持 405，但错误体是统一形状。

    没把它"折算"成 422（`INVALID_REQUEST` 的默认状态码）是刻意的：方法不对
    与参数不对是两件事，客户端据此做的重试/提示也不同。
    """
    r = await client.get(REGISTER_URL)

    assert r.status_code == 405, r.text
    body = r.json()
    assert body["error"]["code"] == "INVALID_REQUEST"
    assert body["request_id"] == r.headers["x-request-id"]


async def test_business_error_shape_is_unchanged(client: httpx.AsyncClient) -> None:
    """加了框架层 handler 之后，业务错误体一个字节都不能变。"""
    r = await client.get("/api/v1/documents/00000000-0000-0000-0000-000000000001")

    assert r.status_code == 401
    body = r.json()
    assert body["error"]["code"] == "UNAUTHENTICATED"
    assert body["error"]["details"] == {}
    assert body["request_id"] == r.headers["x-request-id"]
