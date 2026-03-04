from __future__ import annotations

import json
import math
import random
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .error_catalog import lookup_error_description


class ErrorAction(str, Enum):
    RETRY = "retry"
    STOP = "stop"
    SKIP = "skip"


@dataclass(slots=True)
class RetryPolicy:
    max_retries: int = 5
    base_delay: float = 1.0
    max_delay: float = 30.0
    jitter: float = 0.2
    retry_on_statuses: set[int] = field(default_factory=lambda: {429, 500, 503})
    retry_on_reasons: set[str] = field(
        default_factory=lambda: {
            "rateLimitExceeded",
            "userRateLimitExceeded",
            "backendError",
            "backendNotConnected",
            "notReady",
            "internalError",
            "concurrentLimitExceeded",
        }
    )


@dataclass(slots=True)
class ErrorInfo:
    status: int | None
    reason: str | None
    message: str | None
    domain: str | None
    location: str | None
    location_type: str | None
    description: str | None
    action: ErrorAction
    retryable: bool
    attempt: int
    endpoint: str
    page: int
    origin: str
    params_hash: str | None
    uri_redacted: str | None
    raw_errors: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "message": self.message,
            "domain": self.domain,
            "location": self.location,
            "location_type": self.location_type,
            "description": self.description,
            "action": self.action.value,
            "retryable": self.retryable,
            "attempt": self.attempt,
            "endpoint": self.endpoint,
            "page": self.page,
            "origin": self.origin,
            "params_hash": self.params_hash,
            "uri": self.uri_redacted,
            "raw_errors": self.raw_errors,
        }


class FetchError(RuntimeError):
    def __init__(self, info: ErrorInfo, partial_result: dict[str, Any]) -> None:
        super().__init__(info.message or "Erro ao consultar a API do YouTube")
        self.info = info
        self.partial_result = partial_result


_SKIP_REASONS: dict[str, set[str]] = {
    "commentThreads.list": {
        "commentsDisabled",
        "videoNotFound",
        "commentThreadNotFound",
        "channelNotFound",
    },
    "comments.list": {"commentNotFound"},
    "videos.list": {"videoNotFound"},
    "captions.list": {"captionNotFound", "videoNotFound"},
    "activities.list": {"channelNotFound", "homeChannelNotFound"},
    "channels.list": {"channelNotFound", "categoryNotFound"},
}

_STOP_REASONS: set[str] = {
    "quotaExceeded",
    "dailyLimitExceeded",
    "accessNotConfigured",
    "keyInvalid",
    "keyExpired",
    "insufficientPermissions",
    "forbidden",
    "authorizationRequired",
    "homeParameterDeprecated",
    "channelForbidden",
    "invalidChannelId",
    "invalidLocation",
    "invalidRelevanceLanguage",
    "invalidSearchFilter",
    "invalidCriteria",
    "videoChartNotFound",
    "operationNotSupported",
}


def redact_api_key(uri: str | None) -> str | None:
    if not uri:
        return uri
    return re.sub(r"([?&]key=)[^&]+", r"\1REDACTED", uri)


def parse_error_content(content: bytes | str | None) -> dict[str, Any]:
    if not content:
        return {}
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return {}
    if isinstance(payload, dict):
        return payload
    return {}


def extract_error_details(payload: dict[str, Any]) -> tuple[
    int | None,
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
    list[dict[str, Any]],
]:
    error_obj = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    code = error_obj.get("code")
    message = error_obj.get("message")
    errors = error_obj.get("errors") if isinstance(error_obj.get("errors"), list) else []
    primary = errors[0] if errors else {}
    reason = primary.get("reason")
    domain = primary.get("domain")
    location = primary.get("location")
    location_type = primary.get("locationType")
    return code, reason, message, domain, location, location_type, errors


def classify_error(
    *,
    endpoint: str,
    status: int | None,
    reason: str | None,
    attempt: int,
    retry_policy: RetryPolicy,
) -> tuple[ErrorAction, bool]:
    if reason and reason in _SKIP_REASONS.get(endpoint, set()):
        return ErrorAction.SKIP, False

    if reason == "processingFailure" and endpoint == "commentThreads.list":
        if attempt <= 1:
            return ErrorAction.RETRY, True
        return ErrorAction.STOP, False

    if reason and reason in _STOP_REASONS:
        return ErrorAction.STOP, False

    if status in {401, 403, 400}:
        return ErrorAction.STOP, False

    if status == 404:
        return ErrorAction.SKIP, False

    if status in retry_policy.retry_on_statuses:
        return ErrorAction.RETRY, True

    if reason and reason in retry_policy.retry_on_reasons:
        return ErrorAction.RETRY, True

    if status and status >= 500:
        return ErrorAction.RETRY, True

    return ErrorAction.STOP, False


def build_error_info(
    *,
    endpoint: str,
    status: int | None,
    reason: str | None,
    message: str | None,
    domain: str | None,
    location: str | None,
    location_type: str | None,
    raw_errors: list[dict[str, Any]],
    attempt: int,
    page: int,
    origin: str,
    params_hash: str | None,
    uri: str | None,
    retry_policy: RetryPolicy,
) -> ErrorInfo:
    action, retryable = classify_error(
        endpoint=endpoint,
        status=status,
        reason=reason,
        attempt=attempt,
        retry_policy=retry_policy,
    )
    description = lookup_error_description(endpoint, reason) if reason else None
    return ErrorInfo(
        status=status,
        reason=reason,
        message=message,
        domain=domain,
        location=location,
        location_type=location_type,
        description=description,
        action=action,
        retryable=retryable,
        attempt=attempt,
        endpoint=endpoint,
        page=page,
        origin=origin,
        params_hash=params_hash,
        uri_redacted=redact_api_key(uri),
        raw_errors=raw_errors,
    )


def compute_backoff_seconds(
    attempt: int,
    policy: RetryPolicy,
    retry_after: float | None = None,
) -> float:
    if retry_after is not None:
        return max(0.0, min(policy.max_delay, retry_after))

    base = policy.base_delay * math.pow(2, max(attempt - 1, 0))
    base = min(base, policy.max_delay)
    jitter = base * policy.jitter
    return max(0.0, base + random.uniform(-jitter, jitter))
