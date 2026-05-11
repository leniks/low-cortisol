from typing import Any

from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel


class NullableUsageChatCompletionsModel(OpenAIChatCompletionsModel):
    async def _fetch_response(self, *args: Any, **kwargs: Any) -> Any:
        response = await super()._fetch_response(*args, **kwargs)
        if isinstance(response, tuple):
            return response

        usage = getattr(response, "usage", None)
        if usage is not None:
            _normalize_usage(usage)
        return response


def _normalize_usage(usage: Any) -> None:
    prompt_tokens = _coerce_int(getattr(usage, "prompt_tokens", None))
    completion_tokens = _coerce_int(getattr(usage, "completion_tokens", None))
    total_tokens = getattr(usage, "total_tokens", None)
    if total_tokens is None:
        total_tokens = prompt_tokens + completion_tokens

    _set_attr(usage, "prompt_tokens", prompt_tokens)
    _set_attr(usage, "completion_tokens", completion_tokens)
    _set_attr(usage, "total_tokens", _coerce_int(total_tokens))


def _coerce_int(value: Any) -> int:
    return value if isinstance(value, int) else 0


def _set_attr(target: Any, name: str, value: int) -> None:
    try:
        setattr(target, name, value)
    except Exception:
        pass
