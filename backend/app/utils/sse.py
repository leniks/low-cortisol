import json


def to_sse_data(event: dict[str, object]) -> str:
    encoded = json.dumps(event, ensure_ascii=False)
    return f"data: {encoded}\n\n"
