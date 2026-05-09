import json


def to_sse_data(data: dict[str, object]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

