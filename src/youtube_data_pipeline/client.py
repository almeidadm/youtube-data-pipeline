from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .config import YouTubeDataConfig
from .credits import CreditMonitor
from .endpoints.definitions import DEFAULT_ENDPOINTS, EndpointDefinition
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

    @classmethod
    def from_config(cls, config: YouTubeDataConfig) -> "YouTubeDataClient":
        configure_logging(log_path=config.normalized_log_path())
        from googleapiclient.discovery import build

        service = build(
            "youtube",
            "v3",
            developerKey=config.api_key,
            cache_discovery=config.discovery_cache,
        )
        run_id = config.run_id or uuid4().hex
        storage = ResponseStorage(
            root=config.normalized_storage_root(),
            run_id=run_id,
            compress=config.compress_responses,
        )
        return cls(service=service, storage=storage, run_id=run_id)

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

        limit_reached = False
        stopped_reason: str | None = None

        for page_idx in range(1, max_pages + 2):
            if credit_limit is not None:
                projected = self.credit_monitor.total_spent + definition.base_credit_cost
                if projected > credit_limit:
                    limit_reached = True
                    stopped_reason = "credit_limit"
                    break
            self.request_seq += 1
            request_id = f"{self.run_id}-{self.request_seq:06d}"
            payload = self._execute_request(definition, request_params)
            responses.append(payload)
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

            credit_state = self.credit_monitor.register(endpoint, definition.base_credit_cost)
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
            if not next_token:
                break
            request_params["pageToken"] = next_token

        return {
            "endpoint": endpoint,
            "timestamp": timestamp,
            "responses": responses,
            "files": files,
            "credit_state": self.credit_monitor.snapshot(),
            "limit_reached": limit_reached,
            "stopped_reason": stopped_reason,
        }

    def _execute_request(
        self,
        definition: EndpointDefinition,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        resource = getattr(self.service, definition.resource)()
        method = getattr(resource, definition.method)
        return method(**params).execute()

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
