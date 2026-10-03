import os

from dotenv import load_dotenv
from groq import Groq

load_dotenv()


DEFAULT_MODEL = "openai/gpt-oss-120b"

# Tried in order when the configured model is missing or decommissioned on the
# account (Groq retires model ids regularly). Any of these reads rider messages
# well; the extraction is checked by code afterwards anyway.
FALLBACK_MODELS = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]

# Errors that mean "try another model": the id is gone, or this model's
# per-minute quota is used up (Groq limits are per model, so the next one has
# its own budget).
MODEL_ERROR_MARKERS = (
    "model_not_found", "does not exist", "decommissioned", "not supported", "invalid model",
    "rate limit", "rate_limit", "error code: 429", "over capacity", "error code: 503",
)


class LLMError(Exception):
    pass


class LLMClient:
    """Groq chat completions in JSON mode.

    Optional: without GROQ_API_KEY `available` is False and the extractor
    falls back to its rule-based reader, so the service still runs. Timeouts
    are short because the messaging vendor retries after ~10 seconds.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 6.0,
    ):
        self.api_key = api_key if api_key is not None else os.getenv("GROQ_API_KEY", "")
        self.model = model or os.getenv("GROQ_MODEL") or DEFAULT_MODEL
        self.timeout = timeout
        self._client: Groq | None = None

        if self.api_key:
            self._client = Groq(api_key=self.api_key, timeout=timeout, max_retries=0)

    @property
    def available(self) -> bool:
        return self._client is not None

    def _candidates(self) -> list[str]:
        return [self.model] + [m for m in FALLBACK_MODELS if m != self.model]

    def _call(self, model: str, system_prompt: str, user_prompt: str) -> str:
        assert self._client is not None
        extra = {"reasoning_effort": "low"} if "gpt-oss" in model else {}
        response = self._client.chat.completions.create(
            model=model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            **extra,
        )
        content = response.choices[0].message.content
        if not content:
            raise LLMError(f"{model}: empty completion")
        return content

    def complete_json(self, system_prompt: str, user_prompt: str) -> str:
        if self._client is None:
            raise LLMError("GROQ_API_KEY is not set")

        last_error: Exception | None = None
        for model in self._candidates():
            try:
                content = self._call(model, system_prompt, user_prompt)
                self.model = model  # remember what works
                return content
            except LLMError:
                raise
            except Exception as exc:  # network, auth, rate limit, model errors
                last_error = exc
                text = str(exc).lower()
                if any(marker in text for marker in MODEL_ERROR_MARKERS):
                    continue  # this model id is gone: try the next one
                raise LLMError(f"{model}: {type(exc).__name__}: {exc}") from exc

        raise LLMError(f"no usable model: {type(last_error).__name__}: {last_error}")
