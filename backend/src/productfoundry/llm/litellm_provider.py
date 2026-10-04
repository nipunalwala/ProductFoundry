"""The only module that imports LiteLLM."""

import threading

import litellm

from productfoundry.llm.types import ProviderError, ProviderRequest, ProviderResponse

litellm.telemetry = False
litellm.drop_params = True  # ignore options a provider does not support

_ERROR_DETAIL_LIMIT = 300
_DEADLINE_GRACE = 5.0


class LiteLLMProvider:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        response = self._call_with_deadline(request)
        usage = getattr(response, "usage", None)
        return ProviderResponse(
            text=response.choices[0].message.content or "",
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )

    def _call_with_deadline(self, request: ProviderRequest):
        """Run the call with a limit on its total time.

        The HTTP timeout only limits the gap between bytes, and a provider that
        sends keep-alive bytes while a request waits in its queue never trips it.
        """
        outcome: dict[str, object] = {}

        def call() -> None:
            try:
                outcome["response"] = litellm.completion(
                    model=request.model,
                    messages=request.messages,
                    response_format={"type": "json_object"},
                    timeout=request.timeout,
                    num_retries=0,  # the gateway owns retries and fallback
                    api_key=self._api_key,
                )
            except Exception as exc:
                outcome["error"] = exc

        thread = threading.Thread(target=call, daemon=True)
        thread.start()
        thread.join(request.timeout + _DEADLINE_GRACE)
        if thread.is_alive():
            raise ProviderError("timeout", f"no answer within {request.timeout:.0f} seconds")
        if "error" in outcome:
            # `from None`: the original exception can carry the whole request.
            raise self._translate(outcome["error"]) from None
        return outcome["response"]

    def _translate(self, exc: Exception) -> ProviderError:
        errors = litellm.exceptions
        status = getattr(exc, "status_code", None)
        if isinstance(exc, errors.RateLimitError) or status == 429:
            kind = "rate_limit"
        elif isinstance(exc, errors.Timeout) or status == 408:
            kind = "timeout"
        elif isinstance(
            exc,
            errors.InternalServerError | errors.ServiceUnavailableError | errors.APIConnectionError,
        ) or (isinstance(status, int) and status >= 500):
            kind = "server_error"
        else:
            kind = "rejected"
        # The start of the provider's message, with the key removed should it be echoed.
        message = " ".join(str(exc).split()).replace(self._api_key, "[key]")
        detail = type(exc).__name__ + (f" (HTTP {status})" if status else "")
        return ProviderError(kind, f"{detail}: {message[:_ERROR_DETAIL_LIMIT]}")
