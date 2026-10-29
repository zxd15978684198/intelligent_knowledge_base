"""密码哈希的红线：明文不落库、校验恒定行为、超长输入不被静默截断。"""

from __future__ import annotations

import pytest

from pyknora.services.auth.passwords import (
    MAX_PASSWORD_BYTES,
    hash_password,
    password_within_limit,
    verify_password,
)


def test_hash_is_not_the_plaintext_and_uses_bcrypt_format() -> None:
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    # $2b$ = bcrypt 2b 变体；前缀固定才能说明"库里存的是什么算法"，迁移时靠它识别。
    assert hashed.startswith("$2b$")
    assert len(hashed) <= 255


def test_verify_accepts_right_password_and_rejects_wrong_one() -> None:
    hashed = hash_password("s3cret-pass")
    assert verify_password("s3cret-pass", hashed) is True
    assert verify_password("s3cret-pas", hashed) is False


def test_salt_makes_two_hashes_of_the_same_password_different() -> None:
    """同密码两次哈希必须不同值：相同就说明没加盐，彩虹表直接可用。"""
    assert hash_password("same-password") != hash_password("same-password")


def test_password_over_72_bytes_is_rejected_not_truncated() -> None:
    """bcrypt 5.x 的硬上限。静默截断会让"只在前 72 字节之后不同"的两个密码等价。"""
    too_long = "a" * (MAX_PASSWORD_BYTES + 1)
    assert password_within_limit(too_long) is False
    with pytest.raises(ValueError):
        hash_password(too_long)
    assert verify_password(too_long, hash_password("a" * 10)) is False


def test_multibyte_password_limit_is_measured_in_bytes() -> None:
    """24 个汉字 = 72 字节，正好在上限上；25 个就超了。"""
    assert password_within_limit("密" * 24) is True
    assert password_within_limit("密" * 25) is False


def test_corrupted_hash_is_a_failed_login_not_an_exception() -> None:
    assert verify_password("whatever", "not-a-bcrypt-hash") is False
