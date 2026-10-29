"""密码哈希（bcrypt）。纯函数；异步包装只负责把阻塞计算挪出事件循环。

两个坑写在这里：

1. **bcrypt 5.x 对超过 72 字节的输入直接抛 ValueError**（旧版是静默截断）。静默截断
   意味着"两段只有 72 字节之后不同的密码"会被判成同一个，所以宁可在入口拒绝。
   注意是**字节**不是字符：8 个汉字是 24 字节，72 字节约等于 24 个汉字。
2. `bcrypt.hashpw` 是 CPU 密集操作（默认 12 轮，约几十到上百毫秒）。在 async 路由里
   直接调用会阻塞整个事件循环——并发登录时所有人都卡在这一行。所以对外只用
   `hash_password_async` / `verify_password_async`，内部走 `asyncio.to_thread`。
"""

from __future__ import annotations

import asyncio

import bcrypt

# bcrypt 算法本身的输入上限（字节）。
MAX_PASSWORD_BYTES = 72
# bcrypt 的 cost 因子，默认 12。降低它会让哈希变快，也一并降低暴力破解成本。
_BCRYPT_ROUNDS = 12


def password_within_limit(password: str) -> bool:
    return len(password.encode("utf-8")) <= MAX_PASSWORD_BYTES


def hash_password(password: str) -> str:
    """同步版本，只给脚本与测试用；路由里请用 `hash_password_async`。"""
    if not password_within_limit(password):
        raise ValueError(f"password exceeds {MAX_PASSWORD_BYTES} bytes")
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(_BCRYPT_ROUNDS)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """校验。哈希串损坏时返回 False 而不是抛错——损坏的哈希只应表现为"登录失败"。"""
    if not password_within_limit(password):
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


async def hash_password_async(password: str) -> str:
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password: str, password_hash: str) -> bool:
    return await asyncio.to_thread(verify_password, password, password_hash)
