"""Gemini-first assistant model routing with quota-aware OpenAI fallback."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Sequence

from django.conf import settings
from langchain_core.runnables import RunnableLambda

from main.agent.rate_limits import (
    get_provider_cooldown_seconds,
    record_assistant_signal,
    set_provider_cooldown,
)

logger = logging.getLogger(__name__)


class AssistantLlmUnavailable(Exception):
    """Raised when the assistant LLM cannot complete a request safely."""


@dataclass(frozen=True)
class _ModelTarget:
    provider: str
    model_name: str
    client: Any


def _load_gemini_chat_model():
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI


def _load_openai_chat_model():
    from langchain_openai import ChatOpenAI

    return ChatOpenAI


def _is_gemini_quota_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    quota_markers = (
        "resourceexhausted",
        "resource_exhausted",
        "resource exhausted",
        "quota exceeded",
        "quota exhausted",
        "quota",
        "daily limit",
        "per day",
    )
    auth_markers = (
        "permissiondenied",
        "permission denied",
        "unauthenticated",
        "401",
        "403",
        "api key not valid",
        "invalid argument",
    )
    if any(marker in text for marker in auth_markers):
        return False
    return any(marker in text for marker in quota_markers)


def build_assistant_llm(*, request_id: str, user_id: int):
    """Build the assistant LLM router for one request."""

    return AssistantLlmRouter(request_id=request_id, user_id=user_id)


class AssistantLlmRouter:
    """
    Gemini-first tool-calling wrapper that can fail over one model call at a time.

    The router only falls back on explicit Gemini quota exhaustion so non-quota
    failures still surface as temporary assistant unavailability instead of being
    silently masked by another provider.
    """

    def __init__(self, *, request_id: str, user_id: int):
        self.request_id = request_id
        self.user_id = user_id

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any):
        primary_target: _ModelTarget | None = None
        primary_bound: Any | None = None
        fallback_target: _ModelTarget | None = None
        fallback_bound: Any | None = None

        def _ensure_primary():
            nonlocal primary_target, primary_bound
            if primary_bound is None:
                primary_target = self._build_primary_target()
                primary_bound = primary_target.client.bind_tools(tools, **kwargs)
            return primary_target, primary_bound

        def _ensure_fallback():
            nonlocal fallback_target, fallback_bound
            if fallback_bound is None:
                fallback_target = self._build_fallback_target()
                fallback_bound = fallback_target.client.bind_tools(tools, **kwargs)
            return fallback_target, fallback_bound

        def _invoke(input_value: Any, config: Any = None):
            cooldown_seconds = get_provider_cooldown_seconds("gemini")
            if cooldown_seconds > 0:
                fallback, fallback_runnable = _ensure_fallback()
                record_assistant_signal("llm_primary_skipped_cooldown")
                logger.info(
                    "assistant_llm_primary_skipped_cooldown request_id=%s user_id=%s provider=%s cooldown_seconds=%s fallback_provider=%s fallback_model=%s",
                    self.request_id,
                    self.user_id,
                    "gemini",
                    cooldown_seconds,
                    fallback.provider,
                    fallback.model_name,
                )
                try:
                    return self._invoke_model(fallback_runnable, fallback, input_value, config)
                except Exception as fallback_exc:
                    logger.exception(
                        "assistant_llm_failure request_id=%s user_id=%s provider=%s model=%s fallback_used=true error=%s",
                        self.request_id,
                        self.user_id,
                        fallback.provider,
                        fallback.model_name,
                        fallback_exc,
                    )
                    raise AssistantLlmUnavailable("Fallback assistant model failed.") from fallback_exc

            primary, primary_runnable = _ensure_primary()
            logger.info(
                "assistant_llm_selected request_id=%s user_id=%s provider=%s model=%s",
                self.request_id,
                self.user_id,
                primary.provider,
                primary.model_name,
            )
            try:
                return self._invoke_model(primary_runnable, primary, input_value, config)
            except Exception as exc:
                if not _is_gemini_quota_error(exc):
                    logger.exception(
                        "assistant_llm_failure request_id=%s user_id=%s provider=%s model=%s fallback_used=false error=%s",
                        self.request_id,
                        self.user_id,
                        primary.provider,
                        primary.model_name,
                        exc,
                    )
                    raise AssistantLlmUnavailable("Primary assistant model failed.") from exc

                cooldown_seconds = settings.ASSISTANT_GEMINI_QUOTA_COOLDOWN_SECONDS
                set_provider_cooldown(primary.provider, cooldown_seconds)
                fallback, fallback_runnable = _ensure_fallback()
                record_assistant_signal("llm_fallback_triggered")
                logger.warning(
                    "assistant_llm_fallback_triggered request_id=%s user_id=%s from_provider=%s from_model=%s to_provider=%s to_model=%s cooldown_seconds=%s error=%s",
                    self.request_id,
                    self.user_id,
                    primary.provider,
                    primary.model_name,
                    fallback.provider,
                    fallback.model_name,
                    cooldown_seconds,
                    exc,
                )
                try:
                    return self._invoke_model(fallback_runnable, fallback, input_value, config)
                except Exception as fallback_exc:
                    logger.exception(
                        "assistant_llm_failure request_id=%s user_id=%s provider=%s model=%s fallback_used=true error=%s",
                        self.request_id,
                        self.user_id,
                        fallback.provider,
                        fallback.model_name,
                        fallback_exc,
                    )
                    raise AssistantLlmUnavailable("Both assistant models failed.") from fallback_exc

        return RunnableLambda(_invoke, name="assistant_llm_failover")

    def _build_primary_target(self) -> _ModelTarget:
        if not settings.GEMINI_API_KEY:
            raise AssistantLlmUnavailable("GEMINI_API_KEY is not configured.")
        try:
            gemini_chat_model = _load_gemini_chat_model()
            client = gemini_chat_model(
                model=settings.ASSISTANT_PRIMARY_MODEL,
                google_api_key=settings.GEMINI_API_KEY,
                temperature=0,
            )
        except Exception as exc:
            raise AssistantLlmUnavailable("Gemini assistant model could not be initialized.") from exc
        return _ModelTarget(
            provider="gemini",
            model_name=settings.ASSISTANT_PRIMARY_MODEL,
            client=client,
        )

    def _build_fallback_target(self) -> _ModelTarget:
        if not settings.OPENAI_API_KEY:
            raise AssistantLlmUnavailable("OPENAI_API_KEY is not configured.")
        try:
            openai_chat_model = _load_openai_chat_model()
            client = openai_chat_model(
                model=settings.ASSISTANT_FALLBACK_MODEL,
                temperature=0,
            )
        except Exception as exc:
            raise AssistantLlmUnavailable("OpenAI fallback model could not be initialized.") from exc
        return _ModelTarget(
            provider="openai",
            model_name=settings.ASSISTANT_FALLBACK_MODEL,
            client=client,
        )

    @staticmethod
    def _invoke_model(bound_model: Any, target: _ModelTarget, input_value: Any, config: Any):
        logger.debug(
            "assistant_llm_bound_invoke provider=%s model=%s has_config=%s",
            target.provider,
            target.model_name,
            config is not None,
        )
        if config is None:
            return bound_model.invoke(input_value)
        return bound_model.invoke(input_value, config=config)
