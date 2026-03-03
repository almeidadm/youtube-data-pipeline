from typing import Any, Dict


DEFAULT_COST = 1


def estimate_quota(resource: str, params: Dict[str, Any]) -> int:
    name = resource.lower()
    if name == "search":
        return 100
    if name in {"channels", "videos", "playlistitems", "commentthreads", "comments", "playlists"}:
        return 1
    if name == "subscriptions":
        return 1
    if name == "activities":
        return 1
    return DEFAULT_COST
