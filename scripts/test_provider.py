r"""Probe provider compatibility without printing credentials or response bodies.

Run: .venv\Scripts\python.exe scripts/test_provider.py --api both
Each probe sends a real model request and may incur provider charges.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
PARAMETERS = {
    "type": "object",
    "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
    "required": ["a", "b"],
    "additionalProperties": False,
}
FUNCTION = {"name": "multiply", "description": "Multiply two numbers.",
            "parameters": PARAMETERS, "strict": True}


def cases(api):
    user = {"role": "user", "content": "Reply with OK."}
    system = {"role": "system", "content": "Be concise."}
    if api == "responses":
        yield "basic", {"input": [user]}
        yield "instructions", {"input": [user], "instructions": "Be concise."}
        yield "system-message", {"input": [system, user]}
        yield "tool-call", {
            "input": "Call multiply with a=6 and b=7.",
            "tools": [{"type": "function", **FUNCTION}],
            "tool_choice": {"type": "function", "name": "multiply"},
        }
    else:
        yield "basic", {"messages": [user]}
        yield "system-message", {"messages": [system, user]}
        yield "tool-call", {
            "messages": [{"role": "user", "content": "Call multiply with a=6 and b=7."}],
            "tools": [{"type": "function", "function": FUNCTION}],
            "tool_choice": {"type": "function", "function": {"name": "multiply"}},
        }


def valid_output(data, api, tool):
    if api == "responses":
        items = data.get("output", [])
        calls = [i for i in items if i.get("type") == "function_call"]
        text = any(c.get("text") for i in items if i.get("type") == "message"
                   for c in i.get("content", []) if c.get("type") == "output_text")
    else:
        message = data.get("choices", [{}])[0].get("message", {})
        calls = [c.get("function", {}) for c in message.get("tool_calls", [])]
        text = bool(message.get("content"))
    if not tool:
        return text
    return any(c.get("name") == "multiply" and
               json.loads(c.get("arguments", "{}")) == {"a": 6, "b": 7} for c in calls)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", choices=["responses", "chat", "both"], default="responses")
    parser.add_argument("--model", help="Override OPENAI_MODEL for this run only")
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--case", choices=["basic", "instructions", "system-message", "tool-call"],
                        help="Run only one probe type to reduce requests")
    parser.add_argument("--interval", type=float, default=2, help="Seconds between probes")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")  # Existing environment variables take precedence, like agent.py.
    key = os.getenv("OPENAI_API_KEY", "").strip()
    model = args.model or os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    base = (os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    proxy = os.getenv("MODEL_PROXY_URL", "").strip() or None
    target = urlsplit(base)
    if not key or key == "your_api_key_here":
        parser.error("Configure OPENAI_API_KEY in the project .env first.")
    if target.scheme not in {"http", "https"} or not target.hostname or target.username or target.query or target.fragment:
        parser.error("OPENAI_BASE_URL must be a plain HTTP(S) URL without credentials or query parameters.")
    print(f"Host: {target.hostname}; model: {model}; explicit proxy: {bool(proxy)}", flush=True)
    print("Real requests; no retries. Response bodies and credentials are not printed.", flush=True)
    failures = 0
    first = True
    with httpx.Client(proxy=proxy, timeout=args.timeout,
                      headers={"Authorization": f"Bearer {key}"}) as client:
        for api in (["responses", "chat"] if args.api == "both" else [args.api]):
            endpoint = "responses" if api == "responses" else "chat/completions"
            for name, body in cases(api):
                if args.case and name != args.case:
                    continue
                if not first:
                    time.sleep(max(0, args.interval))
                first = False
                started = time.monotonic()
                status = "-"
                try:
                    response = client.post(f"{base}/{endpoint}", json={
                        "model": model, "stream": False, **body,
                        **({"store": False} if api == "responses" else {}),
                    })
                    status = str(response.status_code)
                    if not response.is_success:
                        reason = {
                            400: "Request rejected (format/model/parameter compatibility)",
                            401: "Authentication rejected", 403: "Access denied",
                            404: "Endpoint or model not found", 429: "Rate limit or quota",
                        }.get(response.status_code, "Provider HTTP error")
                        if "system messages are not allowed" in response.text.lower():
                            reason = "System messages are not allowed"
                        raise ValueError(reason)
                    if not valid_output(response.json(), api, name == "tool-call"):
                        raise ValueError("HTTP succeeded but expected text/tool call was missing")
                    outcome = "PASS"
                except httpx.TimeoutException:
                    outcome = "FAIL: network/read timeout"
                except httpx.RequestError as exc:
                    outcome = f"FAIL: transport error ({type(exc).__name__})"
                except (ValueError, KeyError, TypeError, IndexError) as exc:
                    # Only print our own diagnostic strings, never arbitrary server JSON.
                    reason = str(exc) if type(exc) is ValueError else "Unexpected response format"
                    outcome = f"FAIL: {reason}"
                if outcome != "PASS":
                    failures += 1
                print(f"{api:9} {name:15} HTTP {status:3} {time.monotonic()-started:5.1f}s {outcome}", flush=True)
    print(f"Done: {failures} failed probes. Tool probe checks generation, not the full tool-result round trip.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
