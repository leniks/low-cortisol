import re
from typing import Any


_PERIOD_RE = re.compile(
    r"\b(?:19|20)\d{2}\s*[-–]\s*(?:19|20)\d{2}\b|\b(?:19|20)\d{2}\b|за\s+вс[её]\s+время",
    re.IGNORECASE,
)
_DATA_ACTION_RE = re.compile(
    r"\b(вывед|покаж|сравн|посчита|рассчита|найд|таблиц|динамик|график|данн|значени)",
    re.IGNORECASE,
)
_METRIC_HINT_RE = re.compile(
    r"(?:^|\b)(?:како[йеая]|покаж\w*|вывед\w*|найд\w*|посчита\w*|рассчита\w*)\s+"
    r"(?P<hint>.+?)(?=\s+(?:за|в|на|по|для|у|сравн|так\s+же)|[?.!,]|$)",
    re.IGNORECASE,
)
_ENTITY_HINT_RE = re.compile(
    r"(?:\b(?:для|у|по|в|во|с|со)\s+|сравн\w*\s+с\s+)(?P<hint>[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\s.'-]{1,80})",
    re.IGNORECASE,
)
_TRAILING_ENTITY_STOP_RE = re.compile(
    r"\s+(?:за|в|на|по|для|у|с|со|и|так\s+же|тоже|год|годы|году)\b.*$",
    re.IGNORECASE,
)
_NOISE_WORD_RE = re.compile(r"\b(мне|пожалуйста|также|так\s+же|еще|ещё)\b", re.IGNORECASE)


def build_recent_history_signals(
    history: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Return structured history metadata without forwarding raw prior text."""

    recent = list(history)[-limit:]
    first_index = max(len(history) - len(recent), 0)
    signals: list[dict[str, Any]] = []
    for index, item in enumerate(recent, start=first_index):
        role = str(item.get("role") or "user")
        content = str(item.get("content") or "")
        user_signal = extract_request_signal(content) if role == "user" else _empty_signal()
        signals.append(
            {
                "turn_index": index,
                "role": role,
                "content_forwarded": False,
                "content_chars": len(content),
                **user_signal,
            }
        )
    return signals


def build_request_facts(
    message: str,
    history: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    current = extract_request_signal(message)
    history_signals = build_recent_history_signals(history, limit=12)
    previous_user_signals = [
        item for item in reversed(history_signals) if item.get("role") == "user" and item.get("is_data_like")
    ]

    inherited_metrics = _first_non_empty(previous_user_signals, "metrics") if not current["metrics"] else []
    inherited_period = _first_value(previous_user_signals, "period") if not current["period"] else None
    can_inherit_context = bool(current["geographies"] and previous_user_signals)

    return {
        "current": current,
        "history_forwarding": "raw_text_omitted",
        "can_inherit_context": can_inherit_context,
        "inherited_from_recent_user_requests": {
            "metrics": inherited_metrics if can_inherit_context else [],
            "period": inherited_period if can_inherit_context else None,
        },
        "effective": {
            "has_geography": bool(current["geographies"]),
            "has_metric": bool(current["metrics"] or (can_inherit_context and inherited_metrics)),
            "has_period": bool(current["period"] or (can_inherit_context and inherited_period)),
        },
        "instruction": (
            "Use inherited metrics/period only for short follow-up requests with explicit current geography. "
            "Do not request clarification for effective fields marked true."
        ),
    }


def extract_request_signal(message: str) -> dict[str, Any]:
    metric_hint = _extract_metric_hint(message)
    metrics = _metric_values_from_hint(metric_hint)
    geographies = _extract_entity_hints(message)
    for hint in _extract_trailing_entity_hint(metric_hint):
        if hint not in geographies:
            geographies.append(hint)
    period = _extract_period(message)
    return {
        "geographies": geographies,
        "period": period,
        "metrics": metrics,
        "has_geography": bool(geographies),
        "has_period": bool(period),
        "has_metric": bool(metrics),
        "is_data_like": bool(metrics or _DATA_ACTION_RE.search(message)),
    }


def _empty_signal() -> dict[str, Any]:
    return {
        "geographies": [],
        "period": None,
        "metrics": [],
        "has_geography": False,
        "has_period": False,
        "has_metric": False,
        "is_data_like": False,
    }


def _extract_period(message: str) -> str | None:
    match = _PERIOD_RE.search(message)
    return " ".join(match.group(0).split()) if match else None


def _extract_metric_hints(message: str) -> list[str]:
    return _metric_values_from_hint(_extract_metric_hint(message))


def _extract_metric_hint(message: str) -> str:
    match = _METRIC_HINT_RE.search(message)
    if not match:
        return ""
    return _clean_hint(match.group("hint"))


def _metric_values_from_hint(hint: str) -> list[str]:
    if not hint:
        return []
    metric = hint.split()[0] if _extract_trailing_entity_hint(hint) else hint
    return [metric] if metric else []


def _extract_entity_hints(message: str) -> list[str]:
    values: list[str] = []
    for match in _ENTITY_HINT_RE.finditer(message):
        hint = _clean_hint(_TRAILING_ENTITY_STOP_RE.sub("", match.group("hint")))
        if hint and not _PERIOD_RE.fullmatch(hint) and hint not in values:
            values.append(hint)
    return values


def _clean_hint(value: str) -> str:
    cleaned = _NOISE_WORD_RE.sub(" ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,;:-")
    if len(cleaned) < 2:
        return ""
    return cleaned[:120]


def _extract_trailing_entity_hint(metric_hint: str) -> list[str]:
    parts = metric_hint.split()
    if len(parts) < 2:
        return []

    first = parts[0]
    if not (first.isupper() or len(first) <= 4):
        return []

    entity = _clean_hint(" ".join(parts[1:]))
    return [entity] if entity else []


def _first_non_empty(signals: list[dict[str, Any]], key: str) -> list[str]:
    for signal in signals:
        value = signal.get(key)
        if isinstance(value, list) and value:
            return value
    return []


def _first_value(signals: list[dict[str, Any]], key: str) -> str | None:
    for signal in signals:
        value = signal.get(key)
        if isinstance(value, str) and value:
            return value
    return None
