import threading
from dataclasses import dataclass, field


@dataclass(slots=True)
class CreditMonitor:
    total_spent: int = 0
    per_endpoint: dict[str, int] = field(default_factory=dict)
    # register() pode ser chamado por várias threads (coleta paralela); sem lock o
    # `+=` perde incrementos (subcontagem) e o dict pode corromper sob concorrência.
    _lock: "threading.Lock" = field(
        default_factory=threading.Lock, init=False, repr=False, compare=False
    )

    def register(self, endpoint: str, cost: int) -> dict[str, int | dict[str, int]]:
        with self._lock:
            self.total_spent += cost
            self.per_endpoint[endpoint] = self.per_endpoint.get(endpoint, 0) + cost
            return self._snapshot_locked()

    def snapshot(self) -> dict[str, int | dict[str, int]]:
        with self._lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> dict[str, int | dict[str, int]]:
        return {
            "total_spent": self.total_spent,
            "per_endpoint": dict(self.per_endpoint),
        }
