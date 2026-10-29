"""路由表守卫：新端点忘了挂权限依赖时，这里必须红。

为什么需要这条测试：`require_permission` 是**显式**挂在每个路由上的，没有任何机制
阻止新端点漏挂。漏挂不会报错、不会 500，只会安静地对所有人开放——这类缺陷在评审
里极难靠肉眼发现（要一条条数），但机器一秒就能数完。

判定口径：路由的依赖树里**必须有一个** `require_*` 工厂产出的依赖。不能靠"函数体里
自己判权"来替代，因为那种写法无迹可查；确实需要函数体内判权的（要回 404 而不是
403 的接口）必须在 `_UNGUARDED` 里逐条登记理由——登记本本身就是评审面。

为什么直接从各 `router` 模块取路由、而不是从 `create_app()` 取：这个 FastAPI 版本
把 `include_router` 变成惰性的 `_IncludedRouter`（`route.path` 是 `None`），从 app 上
读不到子路由。所以这里分两步：路由清单从模块取（保证能读到 path 与依赖树），
`create_app()` 只用来断言"9 个 v1 router 都挂上了"。
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from pyknora.api.main import create_app
from pyknora.api.v1 import (
    admin,
    auth,
    chat,
    documents,
    jobs,
    knowledge_bases,
    lineage,
    query,
    workspaces,
)

_API_PREFIX = "/api/v1"

_V1_MODULES = (
    admin,
    auth,
    chat,
    documents,
    jobs,
    knowledge_bases,
    lineage,
    query,
    workspaces,
)

#: 允许**没有** `require_*` 依赖的路由，每条写清为什么安全。
#: 键是 `(方法, 完整路径)`，完整路径含 `/api/v1` 前缀与路由模板里的花括号。
_UNGUARDED: dict[tuple[str, str], str] = {
    ("POST", f"{_API_PREFIX}/auth/register"): "注册时还没有身份可言",
    ("POST", f"{_API_PREFIX}/auth/login"): "登录的目的就是拿身份",
    ("GET", f"{_API_PREFIX}/auth/me"): "自省只读自己；身份由 get_scope 强制",
    (
        "GET",
        f"{_API_PREFIX}/workspaces/{{ws_id}}/permissions",
    ): "自省只看自己，不需要管理权（否则成员看不到自己的按钮）",
    (
        "PATCH",
        f"{_API_PREFIX}/workspaces/{{ws_id}}/members/{{membership_id}}",
    ): "函数体内按 workspace.manage_members 判权，因为越权要回 404 而非 403",
    (
        "DELETE",
        f"{_API_PREFIX}/workspaces/{{ws_id}}/members/{{membership_id}}",
    ): "函数体内判权：管理权**或**删自己（主动退出），越权回 404 而非 403",
}

_METHODS_TO_SKIP = {"HEAD", "OPTIONS"}


def _api_routes() -> list[tuple[str, APIRoute]]:
    """(完整路径, 路由) 清单，从各 v1 模块的 router 上取。"""
    routes: list[tuple[str, APIRoute]] = []
    for module in _V1_MODULES:
        router = module.router
        prefix = f"{_API_PREFIX}{router.prefix}"
        for route in router.routes:
            if isinstance(route, APIRoute):
                routes.append((f"{prefix}{route.path}", route))
    return routes


def _route_keys(path: str, route: APIRoute) -> list[tuple[str, str]]:
    return [(method, path) for method in sorted(route.methods - _METHODS_TO_SKIP)]


def _permission_dependency_names(route: APIRoute) -> list[str]:
    """路由依赖树里 `require_*` 工厂产出的依赖名。"""
    names: list[str] = []
    for dependency in route.dependant.dependencies:
        call = dependency.call
        name = getattr(call, "__name__", "")
        if name.startswith("require_"):
            names.append(name)
    return names


def test_every_route_carries_a_permission_dependency() -> None:
    """未挂权限、又没登记豁免的路由 → 失败，并列出是哪几条。"""
    unguarded: list[tuple[str, str]] = []
    for path, route in _api_routes():
        if _permission_dependency_names(route):
            continue
        for key in _route_keys(path, route):
            if key not in _UNGUARDED:
                unguarded.append(key)

    assert not unguarded, (
        "这些端点没有 require_* 依赖，也没有在 _UNGUARDED 里登记理由："
        f"{unguarded}。要么挂上权限，要么写清它为什么可以公开。"
    )


def test_whitelist_entries_still_exist() -> None:
    """豁免名单不能有"幽灵条目"：路由改名/删掉后，旧条目会让守卫形同虚设。"""
    actual = {key for path, route in _api_routes() for key in _route_keys(path, route)}
    stale = sorted(set(_UNGUARDED) - actual)

    assert not stale, (
        f"这些豁免条目在路由表里已经不存在了（路由被改名或删除？）：{stale}。"
        "留着它们等于给未来的同名路由预留了免检通道。"
    )


def test_all_v1_routers_are_mounted() -> None:
    """9 个 v1 模块的 router 必须真的被 `create_app()` 挂上。

    这一条与上面的守卫互补：守卫能发现"挂了但没权限"，这里能发现"根本没挂"
    （模块写好了却忘了 `include_router`，端点就是 404）。
    """
    app = create_app()
    included = sum(1 for route in app.routes if type(route).__name__ == "_IncludedRouter")

    assert included == len(_V1_MODULES), (
        f"create_app() 只挂了 {included} 个 Router，但 v1 有 {len(_V1_MODULES)} 个模块；"
        "有模块忘了 include_router，它的端点会全部 404。"
    )


def test_guard_sees_more_than_a_handful_of_routes() -> None:
    """防止守卫因为"路由表读成空的"而永远通过——这是断言类测试最常见的假绿。"""
    routes = _api_routes()
    assert len(routes) >= 20, f"只读到 {len(routes)} 条 /api/v1 路由，路由表可能没挂上"

    guarded = [route for _, route in routes if _permission_dependency_names(route)]
    assert len(guarded) >= 15, f"只有 {len(guarded)} 条路由带权限依赖，明显偏低"
