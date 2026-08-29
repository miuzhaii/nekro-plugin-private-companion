import re

TTL_SECONDS = 3600
MAX_PER_USER = 5

_URL_RE = re.compile(r"https?://\S+")
_IPV4_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def redact_error(text: str) -> str:
    redacted = _URL_RE.sub("「[链接已隐藏]」", text)
    redacted = _IPV4_RE.sub("「[地址已隐藏]」", redacted)
    return redacted[:120]


def enqueue(queue: list, item: dict, now: float, ttl_seconds: int = 3600) -> list:
    user_id = item["user_id"]
    kind = item["kind"]
    payload = {
        "user_id": user_id,
        "kind": kind,
        "ts": now,
        "expire_at": now + ttl_seconds,
    }
    if "motivation" in item:
        payload["motivation"] = item["motivation"]
    if "error" in item and item["error"] is not None:
        payload["error"] = redact_error(item["error"])

    for existing in queue:
        if existing.get("user_id") == user_id and existing.get("kind") == kind:
            existing["ts"] = now
            existing["expire_at"] = now + ttl_seconds
            if "motivation" in payload:
                existing["motivation"] = payload["motivation"]
            if "error" in payload:
                existing["error"] = payload["error"]
            return queue

    user_items = [i for i in queue if i.get("user_id") == user_id]
    if len(user_items) >= MAX_PER_USER:
        oldest = min(user_items, key=lambda i: i.get("ts", 0))
        queue.remove(oldest)

    queue.append(payload)
    return queue


def pop_due(queue: list, now: float):
    remaining = [i for i in queue if i.get("expire_at", 0) > now]
    queue[:] = remaining
    if not queue:
        return None
    earliest = min(queue, key=lambda i: i.get("ts", 0))
    queue.remove(earliest)
    return earliest
