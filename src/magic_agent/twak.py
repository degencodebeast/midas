import json
import subprocess
from collections.abc import Callable


class TwakError(RuntimeError):
    pass


class TwakRunner:
    def __init__(self, run: Callable = subprocess.run) -> None:
        self._run = run
        self.last_redacted_command = ""

    def json(self, args: list[str], *, timeout: float = 60) -> dict:
        command = ["twak", *args]
        redacted = command.copy()
        if "--password" in redacted:
            redacted[redacted.index("--password") + 1] = "[REDACTED]"
        self.last_redacted_command = " ".join(redacted)
        process = self._run(command, capture_output=True, text=True, timeout=timeout, check=False)
        if process.returncode != 0:
            raise TwakError(f"twak exit={process.returncode}")
        try:
            payload = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            raise TwakError("twak returned malformed JSON") from exc
        if payload.get("success") is False or payload.get("error"):
            raise TwakError("twak reported failure")
        return payload
