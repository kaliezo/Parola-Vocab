"""Strict result validation and isolated Codex CLI execution."""

import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = PROJECT_DIR / "result_schema.json"
PROMPT_PATH = PROJECT_DIR / "classifier_prompt.txt"
RESULT_FIELDS = {
    "id", "revision", "original_text", "status", "difficulty", "definition_it",
    "gloss_en", "example_it", "difficulty_reason", "review_note",
}
LIMITS = {"original_text": 120, "definition_it": 500, "gloss_en": 300,
          "example_it": 500, "difficulty_reason": 500, "review_note": 500}


class ClassifierError(Exception):
    pass


class ClassificationCancelled(ClassifierError):
    pass


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"Invalid JSON value: {value}")


def load_json_strict(text):
    try:
        return json.loads(text, object_pairs_hook=_unique_pairs,
                          parse_constant=_reject_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc


def validate_response(response, request):
    if isinstance(response, str):
        response = load_json_strict(response)
    if not isinstance(response, dict) or set(response) != {"schema_version", "request_id", "results"}:
        raise ValueError("Result must contain schema_version, request_id, and results only.")
    if type(response["schema_version"]) is not int or response["schema_version"] != 1:
        raise ValueError("Unsupported classification schema version.")
    if response["request_id"] != request["request_id"]:
        raise ValueError("Result request ID does not match a tracked snapshot.")
    results = response["results"]
    expected = {entry["id"]: entry for entry in request["entries"]}
    if not isinstance(results, list) or len(results) != len(expected):
        raise ValueError("Result must contain exactly one item for every requested entry.")
    seen = set()
    for item in results:
        if not isinstance(item, dict) or set(item) != RESULT_FIELDS:
            raise ValueError("A result item has missing or extra fields.")
        if type(item["id"]) is not int or item["id"] not in expected or item["id"] in seen:
            raise ValueError("Result contains a duplicate or unexpected entry ID.")
        seen.add(item["id"])
        original = expected[item["id"]]
        if type(item["revision"]) is not int or item["revision"] != original["revision"] \
                or item["original_text"] != original["original_text"]:
            raise ValueError("Result does not match the submitted revision and original text.")
        for name, limit in LIMITS.items():
            value = item[name]
            if name == "original_text":
                if not isinstance(value, str) or not value or len(value) > limit:
                    raise ValueError("Result original text is invalid.")
            elif value is not None and (not isinstance(value, str) or len(value) > limit):
                raise ValueError(f"Result {name} must be text of at most {limit} characters or null.")
        if item["status"] == "ok":
            if item["difficulty"] not in ("easy", "medium", "hard"):
                raise ValueError("Successful result needs a valid difficulty.")
            if not all(isinstance(item[field], str) and item[field].strip()
                       for field in ("definition_it", "gloss_en", "example_it", "difficulty_reason")):
                raise ValueError("Successful result is missing learning content or a reason.")
            if item["review_note"] is not None:
                raise ValueError("Successful result must have a null review note.")
        elif item["status"] == "needs_review":
            if item["difficulty"] is not None or not isinstance(item["review_note"], str) \
                    or not item["review_note"].strip():
                raise ValueError("Needs-review result requires null difficulty and a review note.")
        else:
            raise ValueError("Result status must be ok or needs_review.")
    if seen != set(expected):
        raise ValueError("Result has missing or unexpected entries.")
    return results


def build_prompt(request):
    payload = {key: request[key] for key in ("schema_version", "request_id", "level", "entries")}
    return (PROMPT_PATH.read_text(encoding="utf-8") + "\n\nOutput JSON Schema:\n" +
            SCHEMA_PATH.read_text(encoding="utf-8") + "\n\nInput JSON:\n" +
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def safe_child_environment():
    env = os.environ.copy()
    for key in list(env):
        if key.upper().endswith("API_KEY") or key.upper() in (
            "OPENAI_BASE_URL", "OPENAI_API_BASE", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID",
            "CODEX_ACCESS_TOKEN", "CODEX_REFRESH_TOKEN",
        ):
            env.pop(key, None)
    return env


def _stop_process(proc):
    if os.name == "nt":
        try:
            result = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=3, check=False)
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if (result is None or result.returncode != 0) and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        proc.communicate(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "nt":
                proc.kill()
            else:
                os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        proc.communicate()


def explain_failure(output):
    low = output.lower()
    if any(term in low for term in ("not logged in", "login required", "unauthorized", "authentication",
                                    "authorization", "refresh token", "token expired", "401")):
        return "Codex login is missing or expired. Run 'codex login' in a terminal, then retry."
    if any(term in low for term in ("model_not_found", "unknown model", "model is not available", "does not have access to model")):
        return "The selected model is unavailable for this account. Choose another model in Settings and retry."
    if any(term in low for term in ("usage limit", "quota")):
        return "The account's Codex usage limit was reached. Wait for it to reset, then retry."
    if any(term in low for term in ("rate limit", "429", "slow_down", "too many requests")):
        return ("Codex limited this request. This may be temporary traffic throttling or an "
                "account limit. Wait and retry unfinished entries; check account usage if it continues.")
    if any(term in low for term in ("network", "connection", "dns", "timeout", "offline")):
        return "Codex could not connect. Check connectivity, then retry."
    return "Codex failed: " + (output.strip()[-600:] or "no diagnostic output")


def run_codex(request, *, model="gpt-6-sol", effort="high", timeout=900,
              cancel_event=None, executable="codex"):
    """Run a single request. SQLite and Tkinter are never touched in this worker."""
    if effort not in ("low", "medium", "high", "xhigh", "max"):
        raise ValueError("Invalid reasoning effort.")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("Model is required.")
    if not 30 <= int(timeout) <= 3600:
        raise ValueError("Timeout must be 30 to 3600 seconds.")
    resolved_executable = shutil.which(executable)
    if resolved_executable is None:
        raise ClassifierError("Codex CLI was not found. Install the official Codex CLI and run 'codex login'.")
    cancel_event = cancel_event or threading.Event()
    input_text = build_prompt(request)
    with tempfile.TemporaryDirectory(prefix="italian-vocabulary-") as directory:
        job_dir = Path(directory).resolve()
        result_path = job_dir / "result.json"
        args = [resolved_executable, "exec", "--ignore-user-config", "--ignore-rules",
                "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only",
                "--model", model.strip(), "-c", f'model_reasoning_effort="{effort}"',
                "-c", 'forced_login_method="chatgpt"', "-c", 'approval_policy="never"',
                "-c", "mcp_servers={}", "-c", "hooks={}",
                "--cd", str(job_dir), "--output-schema", str(SCHEMA_PATH),
                "--output-last-message", str(result_path), "-"]
        process_options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP,
                            "shell": resolved_executable.lower().endswith((".cmd", ".bat"))}
                           if os.name == "nt" else {"start_new_session": True})
        try:
            proc = subprocess.Popen(args, cwd=job_dir, env=safe_child_environment(),
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, **process_options)
        except OSError as exc:
            raise ClassifierError(f"Could not start Codex: {exc}") from exc
        deadline = time.monotonic() + int(timeout)
        output = errors = ""
        try:
            while True:
                if cancel_event.is_set():
                    raise ClassificationCancelled("Evaluation cancelled. Saved entries remain unchanged.")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ClassifierError("Evaluation timed out. Increase the timeout in Settings and retry.")
                try:
                    output, errors = proc.communicate(input=input_text, timeout=min(0.5, remaining))
                    break
                except subprocess.TimeoutExpired:
                    input_text = None
        except (ClassificationCancelled, ClassifierError):
            _stop_process(proc)
            raise
        except OSError as exc:
            _stop_process(proc)
            raise ClassifierError(f"Codex process failed: {exc}") from exc
        if cancel_event.is_set():
            raise ClassificationCancelled("Evaluation cancelled. Saved entries remain unchanged.")
        if proc.returncode != 0:
            raise ClassifierError(explain_failure((errors + "\n" + output)[-4000:]))
        if not result_path.is_file() or not result_path.stat().st_size:
            raise ClassifierError("Codex returned no final result file. Saved entries remain unchanged.")
        try:
            return load_json_strict(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ClassifierError(f"Codex returned invalid JSON: {exc}") from exc
