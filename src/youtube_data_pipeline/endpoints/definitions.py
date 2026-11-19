from dataclasses import dataclass, field


@dataclass(slots=True)
class EndpointDefinition:
    name: str
    resource: str
    method: str = "list"
    base_credit_cost: int = 1
    required_params: set[str] = field(default_factory=set)
    optional_params: set[str] = field(default_factory=set)

    def validate(self, params: dict) -> None:
        missing = sorted(p for p in self.required_params if p not in params)
        if missing:
            missing_fields = ", ".join(missing)
            message = f"Endpoint '{self.name}' requer parâmetros obrigatórios: {missing_fields}"
            raise ValueError(message)


DEFAULT_ENDPOINTS: dict[str, EndpointDefinition] = {
    "search": EndpointDefinition(
        name="search",
        resource="search",
        base_credit_cost=100,
        required_params={"part"},
        optional_params={
            "q",
            "maxResults",
            "type",
            "pageToken",
            "publishedAfter",
            "publishedBefore",
            "regionCode",
            "relevanceLanguage",
            "videoCategoryId",
            "videoDuration",
            "order",
            "safeSearch",
            "eventType",
        },
    ),
    "videos": EndpointDefinition(
        name="videos",
        resource="videos",
        base_credit_cost=1,
        required_params={"part"},
        optional_params={"id", "chart", "maxResults", "pageToken", "regionCode", "hl"},
    ),
    "comments": EndpointDefinition(
        name="comments",
        resource="comments",
        base_credit_cost=1,
        required_params={"part"},
        optional_params={"id", "parentId", "maxResults", "pageToken", "textFormat"},
    ),
    "channels": EndpointDefinition(
        name="channels",
        resource="channels",
        base_credit_cost=1,
        required_params={"part"},
        optional_params={"id", "forHandle", "forUsername", "maxResults", "pageToken", "hl"},
    ),
    "playlistItems": EndpointDefinition(
        name="playlistItems",
        resource="playlistItems",
        base_credit_cost=1,
        required_params={"part", "playlistId"},
        optional_params={"id", "maxResults", "pageToken", "videoId"},
    ),
    "videoCategories": EndpointDefinition(
        name="videoCategories",
        resource="videoCategories",
        base_credit_cost=1,
        required_params={"part"},
        optional_params={"id", "regionCode", "hl"},
    ),
    "captions": EndpointDefinition(
        name="captions",
        resource="captions",
        base_credit_cost=50,
        required_params={"part", "videoId"},
        optional_params={"id", "pageToken", "maxResults"},
    ),
    "commentThreads": EndpointDefinition(
        name="commentThreads",
        resource="commentThreads",
        base_credit_cost=1,
        required_params={"part"},
        optional_params={
            "videoId",
            "allThreadsRelatedToChannelId",
            "maxResults",
            "pageToken",
            "order",
            "textFormat",
            "searchTerms",
            "moderationStatus",
        },
    ),
}
