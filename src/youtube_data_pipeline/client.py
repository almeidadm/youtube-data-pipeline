import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .config import YouTubeDataConfig
from .credits import CreditMonitor
from .endpoints.definitions import DEFAULT_ENDPOINTS, EndpointDefinition
from .errors import (
    ErrorAction,
    FetchError,
    RetryPolicy,
    build_error_info,
    compute_backoff_seconds,
    extract_error_details,
    parse_error_content,
)
from .logger import configure_logging, get_logger
from .storage import ResponseStorage


@dataclass(slots=True)
class YouTubeDataClient:
    service: Any
    storage: ResponseStorage
    credit_monitor: CreditMonitor = field(default_factory=CreditMonitor)
    endpoints: dict[str, EndpointDefinition] = field(
        default_factory=lambda: dict(DEFAULT_ENDPOINTS)
    )
    run_id: str = field(default_factory=lambda: uuid4().hex)
    request_seq: int = 0
    error_mode: str = "best_effort"
    persist_errors: bool = True
    count_failed_requests: bool = True
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)

    @classmethod
    def from_config(cls, config: YouTubeDataConfig) -> "YouTubeDataClient":
        from googleapiclient.discovery import build

        run_id = config.run_id or uuid4().hex
        storage_root = config.normalized_storage_root()
        configure_logging(log_path=config.resolved_log_path(run_id))

        service = build(
            "youtube",
            "v3",
            developerKey=config.api_key,
            cache_discovery=config.discovery_cache,
        )
        storage = ResponseStorage(
            root=storage_root,
            run_id=run_id,
            compress=config.compress_responses,
        )
        return cls(
            service=service,
            storage=storage,
            run_id=run_id,
            error_mode=config.error_mode,
            persist_errors=config.persist_errors,
            count_failed_requests=config.count_failed_requests,
            retry_policy=config.retry_policy,
        )

    def register_endpoint(self, definition: EndpointDefinition) -> None:
        self.endpoints[definition.name] = definition

    def fetch(
        self,
        endpoint: str,
        params: Mapping[str, Any],
        *,
        max_pages: int = 1,
        origin: str | None = None,
        credit_limit: int | None = None,
    ) -> dict[str, Any]:
        if endpoint not in self.endpoints:
            raise KeyError(f"Endpoint '{endpoint}' não está registrado")

        definition = self.endpoints[endpoint]
        definition.validate(dict(params))

        origin_value, origin_field = self._infer_origin(dict(params))
        if origin is not None:
            origin_value = origin
            origin_field = "explicit"

        logger = get_logger("youtube_data_pipeline.client").bind(
            endpoint=endpoint,
            run_id=self.run_id,
        )
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        request_params = dict(params)
        files: list[str] = []
        responses: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        total_attempts = 0
        total_retries = 0

        limit_reached = False
        stopped_reason: str | None = None
        endpoint_key = f"{definition.resource}.{definition.method}"

        for page_idx in range(1, max_pages + 2):
            attempt = 0
            has_next_page = False
            while True:
                if credit_limit is not None:
                    projected = self.credit_monitor.total_spent + definition.base_credit_cost
                    if projected > credit_limit:
                        limit_reached = True
                        stopped_reason = "credit_limit"
                        break
                attempt += 1
                total_attempts += 1
                self.request_seq += 1
                request_id = f"{self.run_id}-{self.request_seq:06d}"
                try:
                    payload = self._execute_request(definition, request_params)
                    credit_state = self.credit_monitor.register(
                        endpoint, definition.base_credit_cost
                    )
                    save_meta = self.storage.save_with_metadata(
                        endpoint,
                        payload,
                        page_idx=page_idx,
                        timestamp=timestamp,
                        run_id=self.run_id,
                        origin=origin_value,
                        origin_field=origin_field,
                        params=request_params,
                    )
                    file_path = save_meta["path"]
                    files.append(str(file_path))
                    responses.append(payload)
                    logger.info(
                        "youtube_api_request",
                        request_id=request_id,
                        origin=origin_value,
                        origin_field=origin_field,
                        cost=definition.base_credit_cost,
                        cumulative_credits=credit_state["total_spent"],
                        page=page_idx,
                        file=str(file_path),
                        per_endpoint=credit_state["per_endpoint"],
                    )
                    self.storage.append_manifest(
                        {
                            "entry_type": "response",
                            "request_id": request_id,
                            "endpoint": endpoint,
                            "origin": save_meta["origin"],
                            "origin_field": save_meta["origin_field"],
                            "origin_hash": save_meta["origin_hash"],
                            "params_hash": save_meta["params_hash"],
                            "page": page_idx,
                            "file": save_meta["relative_path"],
                            "timestamp": save_meta["timestamp"],
                            "cost": definition.base_credit_cost,
                            "cumulative_credits": credit_state["total_spent"],
                            "per_endpoint": credit_state["per_endpoint"],
                        },
                        run_id=self.run_id,
                    )
                    next_token = payload.get("nextPageToken")
                    if next_token:
                        request_params["pageToken"] = next_token
                        has_next_page = True
                    break
                except Exception as exc:  # pragma: no cover - delega para HttpError abaixo
                    from googleapiclient.errors import HttpError

                    if not isinstance(exc, HttpError):
                        raise

                    if self.count_failed_requests:
                        credit_state = self.credit_monitor.register(
                            endpoint, definition.base_credit_cost
                        )
                    payload_dict = parse_error_content(exc.content)
                    status, reason, message, domain, location, location_type, raw_errors = (
                        extract_error_details(payload_dict)
                    )
                    if status is None and hasattr(exc, "resp") and exc.resp is not None:
                        status = getattr(exc.resp, "status", None)
                    params_hash = self.storage._params_hash(request_params)
                    error_info = build_error_info(
                        endpoint=endpoint_key,
                        status=status,
                        reason=reason,
                        message=message,
                        domain=domain,
                        location=location,
                        location_type=location_type,
                        raw_errors=raw_errors,
                        attempt=attempt,
                        page=page_idx,
                        origin=origin_value,
                        params_hash=params_hash,
                        uri=getattr(exc, "uri", None),
                        retry_policy=self.retry_policy,
                    )
                    error_payload = error_info.to_dict()
                    errors.append(error_payload)
                    logger.error(
                        "youtube_api_error",
                        request_id=request_id,
                        origin_field=origin_field,
                        cost=definition.base_credit_cost,
                        cumulative_credits=credit_state["total_spent"],
                        per_endpoint=credit_state["per_endpoint"],
                        **error_payload,
                    )

                    should_retry = (
                        error_info.action == ErrorAction.RETRY
                        and attempt <= self.retry_policy.max_retries
                    )
                    if should_retry:
                        total_retries += 1
                        retry_after = None
                        if hasattr(exc, "resp") and exc.resp is not None:
                            retry_after = exc.resp.get("retry-after")
                            if retry_after is not None:
                                try:
                                    retry_after = float(retry_after)
                                except (TypeError, ValueError):
                                    retry_after = None
                        backoff = compute_backoff_seconds(
                            attempt=attempt,
                            policy=self.retry_policy,
                            retry_after=retry_after,
                        )
                        logger.info(
                            "youtube_api_retry",
                            request_id=request_id,
                            attempt=attempt,
                            backoff_seconds=backoff,
                            status=error_info.status,
                            reason=error_info.reason,
                        )
                        time.sleep(backoff)
                        continue

                    if self.persist_errors:
                        error_payload = error_info.to_dict()
                        error_meta = self.storage.save_error(
                            endpoint,
                            error_payload,
                            page_idx=page_idx,
                            timestamp=timestamp,
                            run_id=self.run_id,
                            origin=origin_value,
                            origin_field=origin_field,
                            params=request_params,
                        )
                        self.storage.append_manifest(
                            {
                                "entry_type": "error",
                                "request_id": request_id,
                                "endpoint": endpoint,
                                "origin": error_meta["origin"],
                                "origin_field": error_meta["origin_field"],
                                "origin_hash": error_meta["origin_hash"],
                                "params_hash": error_meta["params_hash"],
                                "page": page_idx,
                                "file": error_meta["relative_path"],
                                "timestamp": error_meta["timestamp"],
                                "cost": definition.base_credit_cost,
                                "cumulative_credits": credit_state["total_spent"],
                                "per_endpoint": credit_state["per_endpoint"],
                                "error_status": error_info.status,
                                "error_reason": error_info.reason,
                                "error_message": error_info.message,
                                "error_description": error_info.description,
                                "error_action": error_info.action.value,
                                "error_retryable": error_info.retryable,
                            },
                            run_id=self.run_id,
                        )

                    stopped_reason = self._build_stopped_reason(error_info)
                    if self.error_mode == "raise":
                        raise FetchError(
                            error_info,
                            {
                                "endpoint": endpoint,
                                "timestamp": timestamp,
                                "responses": responses,
                                "files": files,
                                "credit_state": self.credit_monitor.snapshot(),
                                "limit_reached": limit_reached,
                                "stopped_reason": stopped_reason,
                                "errors": errors,
                                "attempt_count": total_attempts,
                                "retry_count": total_retries,
                            },
                        )
                    break

            if limit_reached or stopped_reason:
                break
            if not has_next_page:
                break

        return {
            "endpoint": endpoint,
            "timestamp": timestamp,
            "responses": responses,
            "files": files,
            "credit_state": self.credit_monitor.snapshot(),
            "limit_reached": limit_reached,
            "stopped_reason": stopped_reason,
            "errors": errors,
            "attempt_count": total_attempts,
            "retry_count": total_retries,
        }

    def _execute_request(
        self,
        definition: EndpointDefinition,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        resource = getattr(self.service, definition.resource)()
        method = getattr(resource, definition.method)
        return method(**params).execute()

    def _build_stopped_reason(self, error_info: Any) -> str:
        if error_info.reason:
            return f"error_{error_info.reason}"
        if error_info.status is not None:
            return f"error_status_{error_info.status}"
        return "error_unknown"

    def _infer_origin(self, params: dict[str, Any]) -> tuple[str, str]:
        candidates = (
            "q",
            "videoId",
            "parentId",
            "id",
            "forHandle",
            "forUsername",
            "allThreadsRelatedToChannelId",
        )
        for key in candidates:
            if key not in params:
                continue
            value = params[key]
            if value is None or value == "":
                continue
            if isinstance(value, (list, tuple, set)):
                return ",".join(str(item) for item in value), key
            return str(value), key
        return "unknown", "unknown"
