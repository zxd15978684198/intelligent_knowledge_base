"""视觉通道配置的回落语义。

为什么单独为三行配置写测试：`resolve_vision()` 的"逐项回落"是一个容易被后续重构
改坏的不变量——最自然的写法是"三项都配了才用 vision，否则整组回落"，那样只换模型名
的部署会静默用回 `chat_model`，而调用方看不出任何异常。这类错误在集成测试里也测不到
（能跑通、只是模型不对），只能在配置层钉死。
"""

from __future__ import annotations

from pyknora.core.settings import Settings


def _settings(**overrides: object) -> Settings:
    """构造一份不读 `.env` 以外的真实值的配置。

    `env="test"` 是为了绕过 `_guard_production_secrets`（`_INSECURE_ENVS` 只认
    dev/test/local）：那些护栏针对的是生产密钥，与本文件要测的回落语义无关，让它们
    在这里失败只会掩盖真正的断言。
    """
    base: dict[str, object] = {
        "env": "test",
        "chat_base_url": "https://chat.example/v1",
        "chat_api_key": "chat-key",
        "chat_model": "chat-model",
        "vision_base_url": "",
        "vision_api_key": "",
        "vision_model": "",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_vision_defaults_to_chat_section() -> None:
    """三项全空时整体回落到 chat_*——这是"只配一套"的部署形态。"""
    assert _settings().resolve_vision() == (
        "https://chat.example/v1",
        "chat-key",
        "chat-model",
    )


def test_vision_falls_back_field_by_field() -> None:
    """只换模型名时，地址与 key 仍取 chat_*（逐项回落，不是整组回落）。"""
    settings = _settings(vision_model="vision-model")

    assert settings.resolve_vision() == (
        "https://chat.example/v1",
        "chat-key",
        "vision-model",
    )


def test_vision_can_point_at_another_gateway() -> None:
    """整套换供应商：三项都配时一个都不回落到 chat_*。"""
    settings = _settings(
        vision_base_url="https://vision.example/v1",
        vision_api_key="vision-key",
        vision_model="vision-model",
    )

    assert settings.resolve_vision() == (
        "https://vision.example/v1",
        "vision-key",
        "vision-model",
    )


def test_vision_configured_requires_api_key() -> None:
    """没有 key 就是没配置——调用方据此跳过，而不是发空令牌换回 401。"""
    assert _settings(vision_api_key="", chat_api_key="").vision_configured is False
    assert _settings(chat_api_key="chat-key").vision_configured is True
    assert _settings(vision_api_key="vision-key", chat_api_key="").vision_configured is True


def test_vision_page_timeout_is_stricter_than_chat_timeout() -> None:
    """页级预算必须小于 chat 超时，否则一页就能吃光整份文档的预算。"""
    settings = _settings()

    assert settings.vision_page_timeout_s == 20.0
    assert settings.vision_page_timeout_s < settings.chat_timeout_s
