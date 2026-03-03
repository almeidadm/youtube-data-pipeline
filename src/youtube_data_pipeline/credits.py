from dataclasses import dataclass, field


@dataclass(slots=True)
class CreditMonitor:
    total_spent: int = 0
    per_endpoint: dict[str, int] = field(default_factory=dict)

    def register(self, endpoint: str, cost: int) -> dict[str, int | dict[str, int]]:
        self.total_spent += cost
        self.per_endpoint[endpoint] = self.per_endpoint.get(endpoint, 0) + cost
        return self.snapshot()

    def snapshot(self) -> dict[str, int | dict[str, int]]:
        return {
            "total_spent": self.total_spent,
            "per_endpoint": dict(self.per_endpoint),
        }
