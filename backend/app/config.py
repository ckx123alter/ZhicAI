"""Explicit model selection; secrets stay in backend environment."""
import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse
import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseModel):
    provider: str = "openai_compatible"
    profile: str
    model: str
    base_url: str
    api_key: SecretStr
    timeout: float = Field(default=30, gt=0, le=120)
    method: str = "function_calling"
    thinking: str = "disabled"
    max_tokens: int = Field(default=4096, ge=512, le=16384)


@lru_cache
def load_settings() -> Settings | None:
    load_dotenv(BACKEND_DIR / ".env", override=False)
    selected = os.getenv("ZHICAI_LLM_MODEL", "").strip()
    if not selected:
        return None
    profiles = yaml.safe_load((BACKEND_DIR / "config/llm_profiles.yaml").read_text(encoding="utf-8"))
    if selected not in profiles:
        raise RuntimeError("ZHICAI_LLM_MODEL 对应的配置不存在")
    profile = profiles[selected]
    if profile.get("provider") not in ("openai_compatible", "deepseek"):
        raise RuntimeError("不支持的模型提供方")
    if not profile.get("model") or "<" in profile["model"]:
        raise RuntimeError("请填写实际模型名")
    if urlparse(profile.get("base_url", "")).scheme not in ("http", "https"):
        raise RuntimeError("请填写实际模型接口地址")
    key = os.getenv(profile.get("api_key_env", ""), "").strip()
    if not key:
        raise RuntimeError(f"请在 backend/.env 配置 {profile.get('api_key_env', '模型密钥')}")
    if profile.get("method", "function_calling") not in ("function_calling", "json_schema"):
        raise RuntimeError("不支持的结构化输出模式")
    if profile.get("thinking", "disabled") not in ("enabled", "disabled"):
        raise RuntimeError("不支持的 thinking 配置")
    return Settings(profile=selected, provider=profile["provider"], model=profile["model"], base_url=profile["base_url"],
                    api_key=SecretStr(key), timeout=profile.get("timeout", 30),
                    method=profile.get("method", "function_calling"), thinking=profile.get("thinking", "disabled"),
                    max_tokens=profile.get("max_tokens", 4096))
