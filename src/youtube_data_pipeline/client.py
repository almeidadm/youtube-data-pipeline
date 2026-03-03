from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

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

    @classmethod
    def from_config(cls, config: YouTubeDataConfig) -> "YouTubeDataClient":
        configure_logging()
        from googleapiclient.discovery import build

        service = build(
            "youtube",
            "v3",
            developerKey=config.api_key,
            cache_discovery=config.discovery_cache,
        )
        storage = ResponseStorage(root=config.normalized_storage_root())
        return cls(service=service, storage=storage)

    def register_endpoint(self, definition: EndpointDefinition) -> None:
        self.endpoints[definition.name] = definition

    def fetch(
        self,
        endpoint: str,
        params: Mapping[str, Any],
        *,
        max_pages: int = 1,
    ) -> dict[str, Any]:
        if endpoint not in self.endpoints:
            raise KeyError(f"Endpoint '{endpoint}' não está registrado")

        definition = self.endpoints[endpoint]
        definition.validate(dict(params))

        logger = get_logger("youtube_data_pipeline.client").bind(endpoint=endpoint)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        request_params = dict(params)
        files: list[str] = []
        responses: list[dict[str, Any]] = []

        for page_idx in range(1, max_pages + 2):
            payload = self._execute_request(definition, request_params)
            responses.append(payload)
            file_path = self.storage.save(
                endpoint,
                payload,
                page_idx=page_idx,
                timestamp=timestamp,
            )
            files.append(str(file_path))

            credit_state = self.credit_monitor.register(endpoint, definition.base_credit_cost)
            logger.info(
                "youtube_api_request",
                cost=definition.base_credit_cost,
                cumulative_credits=credit_state["total_spent"],
                page=page_idx,
                file=str(file_path),
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
        }

    def _execute_request(
        self,
        definition: EndpointDefinition,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        resource = getattr(self.service, definition.resource)()
        method = getattr(resource, definition.method)
        return method(**params).execute()
