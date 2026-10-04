"""The only module that imports LiteLLM."""

import litellm

from productfoundry.llm.types import ProviderError, ProviderRequest, ProviderResponse

litellm.telemetry = False
litellm.drop_params = True  # ignore options a provider does not support


class LiteLLMProvider:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        try:
            response = litellm.completion(
                model=request.model,
                messages=request.messages,
                response_format={"type": "json_object"},
                temperature=0,
                timeout=request.timeout,
                num_retries=0,  # the gateway owns retries and fallback
                api_key=self._api_key,
            )
        except Exception as exc:
            raise _translate(exc) from None  # `from None`: the cause may quote the request
        usage = getattr(response, "usage", None)
        return ProviderResponse(
            text=response.choices[0].message.content or "",
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )


def _translate(exc: Exception) -> ProviderError:
    errors = litellm.exceptions
    status = getattr(exc, "status_code", None)
    if isinstance(exc, errors.RateLimitError) or status == 429:
        kind = "rate_limit"
    elif isinstance(exc, errors.Timeout) or status == 408:
        kind = "timeout"
    elif isinstance(
        exc, errors.InternalServerError | errors.ServiceUnavailableError | errors.APIConnectionError
    ) or (isinstance(status, int) and status >= 500):
        kind = "server_error"
    else:
        kind = "rejected"
    # The class name and status only: provider messages can echo headers or prompts.
    detail = type(exc).__name__ + (f" (HTTP {status})" if status else "")
    return ProviderError(kind, detail)
