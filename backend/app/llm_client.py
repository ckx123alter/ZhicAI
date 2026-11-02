from functools import lru_cache
from .config import load_settings


@lru_cache
def model_client():
    settings = load_settings()
    if settings is None:
        raise RuntimeError("尚未配置对话模型")
    options = dict(model=settings.model, base_url=settings.base_url,
                   api_key=settings.api_key.get_secret_value(), timeout=settings.timeout,
                   max_retries=0, temperature=0, max_tokens=settings.max_tokens,
                   # Avoid httpx2/Brotli decoder incompatibility in existing Windows envs.
                   default_headers={"Accept-Encoding": "gzip, deflate"})
    if settings.provider == "deepseek":
        from langchain_deepseek import ChatDeepSeek
        return ChatDeepSeek(**options, extra_body={"thinking": {"type": settings.thinking}})
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(**options)


@lru_cache
def intent_client():
    from .agent.schemas import Intent
    return model_client().with_structured_output(Intent, method=load_settings().method)


@lru_cache
def narrative_client():
    # Prose does not need tool-call/JSON coercion. Only intent/commands are schemas.
    return model_client()
