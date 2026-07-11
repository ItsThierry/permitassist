"""Single-model policy for all PermitAssist runtime AI calls."""

from __future__ import annotations

PERMITASSIST_AI_PROVIDER = "openai"
PERMITASSIST_AI_MODEL = "gpt-5.6-luna"
PERMITASSIST_AI_CACHE_NAMESPACE = f"v5:{PERMITASSIST_AI_MODEL}"

_MODEL_ALIASES = frozenset({
    "openai",
    "gpt",
    "luna",
    "gpt-5.6-luna",
})


def require_permitassist_model_override(value: str | None) -> str:
    """Accept only aliases that resolve to the one approved engine model."""
    normalized = str(value or "").strip().lower()
    if not normalized or normalized in _MODEL_ALIASES:
        return PERMITASSIST_AI_MODEL
    raise ValueError(
        f"Unsupported PermitAssist AI model override: {value!r}; "
        f"only {PERMITASSIST_AI_MODEL!r} is enabled"
    )


def create_permitassist_chat_completion(openai_client, **kwargs):
    """Call the approved model and fail closed on routing or parameter drift."""
    if "model" in kwargs:
        raise TypeError("PermitAssist model is fixed; callers must not pass model")
    if "temperature" in kwargs:
        raise TypeError(f"{PERMITASSIST_AI_MODEL} uses the API default temperature")

    response = openai_client.chat.completions.create(
        model=PERMITASSIST_AI_MODEL,
        **kwargs,
    )
    observed = str(getattr(response, "model", "") or "").strip()
    if observed and not (
        observed == PERMITASSIST_AI_MODEL
        or observed.startswith(f"{PERMITASSIST_AI_MODEL}-")
    ):
        raise RuntimeError(
            "OpenAI returned an unexpected PermitAssist model: "
            f"requested={PERMITASSIST_AI_MODEL!r} observed={observed!r}"
        )
    return response
