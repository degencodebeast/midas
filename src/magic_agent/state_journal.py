import hashlib
import json
import os
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path


class IntegrityError(RuntimeError):
    pass


def _json_default(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


class StateJournal:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save(self, payload: dict) -> None:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_json_default)
        wrapper = {"payload": payload, "sha256": hashlib.sha256(body.encode()).hexdigest()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(wrapper, sort_keys=True, default=_json_default), encoding="utf-8")
        os.replace(temp, self.path)

    def load(self) -> dict:
        wrapper = json.loads(self.path.read_text(encoding="utf-8"))
        body = json.dumps(wrapper["payload"], sort_keys=True, separators=(",", ":"), default=_json_default)
        if hashlib.sha256(body.encode()).hexdigest() != wrapper["sha256"]:
            raise IntegrityError("state integrity check failed")
        return wrapper["payload"]
