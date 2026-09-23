import json
from contextlib import contextmanager
from typing import Any, Iterator

import requests

from src.main.ui.i18n import tr
from .exceptions import ProviderConnectionError, ProviderResponseError
from .provider import RequestContext
from .transform import RequestTransform, resolve_provider
from .usage import UsageTracker


class OpenAICompatibleProvider:
    def __init__(self, *, base_url: str, api_key: str, usage_tracker: UsageTracker,
                 transform: RequestTransform | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        if not self.base_url:
            raise ValueError(tr("base_url_empty"))
        self.usage_tracker = usage_tracker
        self.transform = transform if transform is not None else RequestTransform(
            provider=resolve_provider("auto", self.base_url)
        )
        self.session = requests.Session()
        self.session.headers.update({
            "authorization": f"Bearer {api_key}", "content-type": "application/json",
        })

    def close(self) -> None:
        self.session.close()

    def _endpoint(self, resource: str) -> str:
        base = self.base_url.removesuffix("/chat/completions")
        return f"{base}/{resource}"

    def _payload(self, messages: list[dict[str, Any]], *, model: str,
                 temperature: float, tools: list[dict[str, Any]] | None,
                 thinking: bool, reasoning_effort: str, stream: bool) -> dict[str, Any]:
        return self.transform.apply(
            messages, model=model, temperature=temperature, tools=tools,
            thinking=thinking, reasoning_effort=reasoning_effort, stream=stream,
        )

    @contextmanager
    def _request(self, method: str, resource: str, *,
                 request_context: RequestContext | None = None, **kwargs: Any) -> Iterator[requests.Response]:
        response = None
        try:
            if request_context:
                request_context.check()
            response = self.session.request(method, self._endpoint(resource), stream=True, **kwargs)
            if request_context:
                request_context.attach(response.close)
            response.raise_for_status()
            yield response
            if request_context:
                request_context.check()
        except requests.exceptions.JSONDecodeError as exc:
            if request_context:
                request_context.check()
            raise ProviderResponseError(tr("model_api_invalid_json")) from exc
        except requests.RequestException as exc:
            if request_context:
                request_context.check()
            detail = f": {exc.response.text[:2000]}" if exc.response is not None else ""
            error_type = ProviderResponseError if exc.response is not None else ProviderConnectionError
            raise error_type(tr("model_api_request_failed", error=exc, detail=detail)) from exc
        except (ValueError, TypeError, AttributeError, OSError) as exc:
            if request_context:
                request_context.check()
            raise ProviderResponseError(tr("model_api_invalid_json")) from exc
        finally:
            if request_context:
                request_context.detach()
            if response is not None:
                response.close()

    def _validate(self, data: Any, *, stream: bool = False) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise ProviderResponseError(tr("model_api_invalid_json"))
        if data.get("error"):
            raise ProviderResponseError(tr("model_api_returned_error", error=str(data["error"])[:2000]))
        choices = data.get("choices", [])
        field = "delta" if stream else "message"
        if not isinstance(choices, list) or (not stream and not choices) or any(
            not isinstance(choice, dict) or not isinstance(choice.get(field), dict)
            for choice in choices
        ):
            raise ProviderResponseError(tr("model_api_missing_message", data=str(data)[:2000]))
        if data.get("usage") is not None and not isinstance(data["usage"], dict):
            raise ProviderResponseError(tr("model_api_invalid_json"))
        return data

    def _record_usage(self, data: dict[str, Any], context: RequestContext | None) -> None:
        usage = data.get("usage")
        if isinstance(usage, dict):
            self.usage_tracker.record(usage)
            if context:
                context.on_usage(usage)

    def complete(self, messages: list[dict[str, Any]], *, model: str,
                 temperature: float = 0.2, tools: list[dict[str, Any]] | None = None,
                 thinking: bool = False, reasoning_effort: str = "",
                 request_context: RequestContext | None = None) -> dict[str, Any]:
        payload = self._payload(messages, model=model, temperature=temperature, tools=tools,
                                thinking=thinking, reasoning_effort=reasoning_effort, stream=False)
        with self._request("POST", "chat/completions", json=payload, timeout=120,
                           request_context=request_context) as response:
            data = self._validate(response.json())
            if request_context:
                request_context.check()
            self._record_usage(data, request_context)
            return data["choices"][0]["message"]

    def stream(self, messages: list[dict[str, Any]], *, model: str,
               temperature: float = 0.2, tools: list[dict[str, Any]] | None = None,
               thinking: bool = False, reasoning_effort: str = "",
               request_context: RequestContext | None = None) -> Iterator[dict[str, Any]]:
        payload = self._payload(messages, model=model, temperature=temperature, tools=tools,
                                thinking=thinking, reasoning_effort=reasoning_effort, stream=True)
        with self._request("POST", "chat/completions", json=payload, timeout=120,
                           request_context=request_context) as response:
            response.encoding = "utf-8"
            for raw_line in response.iter_lines(chunk_size=1, decode_unicode=True):
                if request_context:
                    request_context.check()
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    chunk = self._validate(json.loads(data), stream=True)
                except json.JSONDecodeError as exc:
                    raise ProviderResponseError(tr("model_api_invalid_sse", data=data[:500])) from exc
                self._record_usage(chunk, request_context)
                yield chunk

    def list_models(self) -> list[str]:
        with self._request("GET", "models", timeout=15) as response:
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("data"), list):
                raise ProviderResponseError(tr("model_api_invalid_json"))
            if any(not isinstance(model, dict) for model in data["data"]):
                raise ProviderResponseError(tr("model_api_invalid_json"))
            return sorted({model["id"] for model in data["data"]
                           if isinstance(model.get("id"), str) and model["id"].strip()})
