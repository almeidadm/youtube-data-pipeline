import gzip
import hashlib
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
from collections.abc import Mapping


@dataclass(slots=True)
class ResponseStorage:
    root: Path
    run_id: str | None = None
    compress: bool = False
    # append_manifest() é chamado por várias threads sobre o mesmo arquivo; serializa
    # para não interleavar linhas JSONL. (Escrita de respostas usa tmp único, abaixo.)
    _manifest_lock: "threading.Lock" = field(
        default_factory=threading.Lock, init=False, repr=False, compare=False
    )

    def _resolve_run_id(self, run_id: str | None) -> str:
        if run_id:
            return run_id
        if self.run_id:
            return self.run_id
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.run_id = f"run-{ts}"
        return self.run_id

    def _origin_hash(self, origin: str) -> str:
        return hashlib.sha256(origin.encode("utf-8")).hexdigest()[:12]

    def _params_hash(self, params: Mapping[str, Any] | None) -> str | None:
        if params is None:
            return None
        payload = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _atomic_write_text(self, path: Path, content: str) -> None:
        # tmp único por escrita: sob coleta paralela duas threads podem mirar o mesmo
        # arquivo final (ex.: origin compartilhada), e um ".tmp" fixo seria corrompido.
        tmp_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(path)

    def _atomic_write_gzip(self, path: Path, content: str) -> None:
        tmp_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
        with gzip.open(tmp_path, "wt", encoding="utf-8") as handle:
            handle.write(content)
        tmp_path.replace(path)

    def save(
        self,
        endpoint: str,
        payload: dict,
        page_idx: int = 1,
        timestamp: str | None = None,
        *,
        run_id: str | None = None,
        origin: str | None = None,
        origin_field: str | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Path:
        run_id = self._resolve_run_id(run_id)
        origin_value = origin or "unknown"
        origin_field = origin_field or "unknown"
        origin_hash = self._origin_hash(origin_value)

        run_dir = self.root / "runs" / run_id
        output_dir = run_dir / f"endpoint={endpoint}" / f"origin={origin_field}:{origin_hash}"
        output_dir.mkdir(parents=True, exist_ok=True)

        suffix = ".json.gz" if self.compress else ".json"
        output_file = output_dir / f"page_{page_idx:03d}{suffix}"
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        if self.compress:
            self._atomic_write_gzip(output_file, content)
        else:
            self._atomic_write_text(output_file, content)
        return output_file

    def save_with_metadata(
        self,
        endpoint: str,
        payload: dict,
        page_idx: int = 1,
        timestamp: str | None = None,
        *,
        run_id: str | None = None,
        origin: str | None = None,
        origin_field: str | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        run_id = self._resolve_run_id(run_id)
        origin_value = origin or "unknown"
        origin_field = origin_field or "unknown"
        origin_hash = self._origin_hash(origin_value)
        params_hash = self._params_hash(params)

        run_dir = self.root / "runs" / run_id
        output_dir = run_dir / f"endpoint={endpoint}" / f"origin={origin_field}:{origin_hash}"
        output_dir.mkdir(parents=True, exist_ok=True)

        suffix = ".json.gz" if self.compress else ".json"
        output_file = output_dir / f"page_{page_idx:03d}{suffix}"
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        if self.compress:
            self._atomic_write_gzip(output_file, content)
        else:
            self._atomic_write_text(output_file, content)

        return {
            "path": output_file,
            "run_id": run_id,
            "origin": origin_value,
            "origin_field": origin_field,
            "origin_hash": origin_hash,
            "params_hash": params_hash,
            "relative_path": str(output_file.relative_to(run_dir)),
            "timestamp": timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        }

    def save_error(
        self,
        endpoint: str,
        payload: dict,
        page_idx: int = 1,
        timestamp: str | None = None,
        *,
        run_id: str | None = None,
        origin: str | None = None,
        origin_field: str | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        run_id = self._resolve_run_id(run_id)
        origin_value = origin or "unknown"
        origin_field = origin_field or "unknown"
        origin_hash = self._origin_hash(origin_value)
        params_hash = self._params_hash(params)

        run_dir = self.root / "runs" / run_id
        output_dir = run_dir / f"endpoint={endpoint}" / f"origin={origin_field}:{origin_hash}"
        output_dir.mkdir(parents=True, exist_ok=True)

        suffix = ".json.gz" if self.compress else ".json"
        output_file = output_dir / f"error_page_{page_idx:03d}{suffix}"
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        if self.compress:
            self._atomic_write_gzip(output_file, content)
        else:
            self._atomic_write_text(output_file, content)

        return {
            "path": output_file,
            "run_id": run_id,
            "origin": origin_value,
            "origin_field": origin_field,
            "origin_hash": origin_hash,
            "params_hash": params_hash,
            "relative_path": str(output_file.relative_to(run_dir)),
            "timestamp": timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        }

    def append_manifest(self, entry: Mapping[str, Any], *, run_id: str | None = None) -> Path:
        run_id = self._resolve_run_id(run_id)
        run_dir = self.root / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = run_dir / "manifest.jsonl"
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with self._manifest_lock:
            with manifest_path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        return manifest_path
