from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pdf2zh_pro.config import SettingsModel
    from pdf2zh_pro.translator.base import BaseTranslator

from pdf2zh_pro.translator.providers import (
    NOT_SUPPORTED_TRANSLATION_ENGINE_SETTING_TYPE,
)
from pdf2zh_pro.translator.providers import TRANSLATION_ENGINE_METADATA
from pdf2zh_pro.translator.providers import TranslateEngineSettingError
from pdf2zh_pro.translator.rate_limit import BaseRateLimiter
from pdf2zh_pro.translator.rate_limit import QPSRateLimiter

logger = logging.getLogger(__name__)


def get_rate_limiter(qps: int | None) -> BaseRateLimiter | None:
    """Create rate limiter based on qps value."""
    if qps and qps > 0:
        return QPSRateLimiter(qps)
    return None


def _create_translator_instance(
    settings: SettingsModel,
    translator_config,
    enforce_glossary_support: bool = True,
    term_extraction: bool = False,
    metrics_collector=None,
    check_cancelled=None,
) -> BaseTranslator:
    """Create translator instance from translator_config.

    Args:
        settings: Global settings model.
        translator_config: Concrete translation engine settings instance.
        enforce_glossary_support: Whether to enforce glossary + LLM support check.
    """
    if isinstance(translator_config, NOT_SUPPORTED_TRANSLATION_ENGINE_SETTING_TYPE):
        raise TranslateEngineSettingError(
            f"{translator_config.translate_engine_type} is not supported, Please use other translator!"
        )

    for metadata in TRANSLATION_ENGINE_METADATA:
        if isinstance(translator_config, metadata.setting_model_type):
            translate_engine_type = metadata.translate_engine_type
            logger.info(f"Using {translate_engine_type} translator")
            model_name = f"pdf2zh_pro.translator.{translate_engine_type.lower()}"
            module = importlib.import_module(model_name)

            if (
                enforce_glossary_support
                and settings.translation.glossaries
                and not metadata.support_llm
            ):
                raise TranslateEngineSettingError(
                    f"{translate_engine_type} does not support glossary. Please choose a different translator or remove the glossary."
                )
            translator_type = getattr(module, f"{translate_engine_type}Translator")
            prefix = "term_" if term_extraction else ""
            qps = getattr(settings.translation, prefix + "qps") or settings.translation.qps
            recommended_qps = getattr(translator_type, "recommended_qps", None)
            if recommended_qps:
                qps = min(qps, recommended_qps) if qps and qps > 0 else recommended_qps
                setattr(settings.translation, prefix + "qps", qps)
                logger.info("Effective %sqps: %s", prefix, qps)
            recommended_pool = getattr(translator_type, "recommended_pool_max_workers", None)
            if recommended_pool:
                pool = (getattr(settings.translation, prefix + "pool_max_workers")
                        or settings.translation.pool_max_workers or recommended_pool)
                pool = min(pool, recommended_pool)
                setattr(settings.translation, prefix + "pool_max_workers", pool)
                logger.info("Effective %spool max workers: %s", prefix, pool)
            rate_limiter = get_rate_limiter(qps)
            temp_settings = settings.model_copy()
            temp_settings.translate_engine_settings = translator_config
            translator = translator_type(temp_settings, rate_limiter)
            if metrics_collector is not None:
                translator.set_metrics_collector(metrics_collector)
            if check_cancelled is not None and hasattr(translator, "check_cancelled"):
                translator.check_cancelled = check_cancelled
            if term_extraction:
                configure = getattr(translator, "configure_for_term_extraction", None)
                if callable(configure):
                    configure()
            return translator

    raise ValueError("No translator found")


def create_translator(settings: SettingsModel, *, metrics_collector=None, check_cancelled=None) -> BaseTranslator:
    """Get main translator instance according to translate_engine_settings."""
    translator_config = settings.translate_engine_settings
    return _create_translator_instance(
        settings=settings,
        translator_config=translator_config,
        enforce_glossary_support=True,
        metrics_collector=metrics_collector,
        check_cancelled=check_cancelled,
    )


def probe_translator(translator):
    """Explicit preflight; construction alone must not send a model request."""
    health_check = getattr(translator, "health_check", None)
    if callable(health_check):
        health_check()
    else:
        translator.translate("Hello", ignore_cache=True)
    return translator


def prepare_translator(settings, *, metrics_collector=None, check_cancelled=None):
    return probe_translator(create_translator(settings, metrics_collector=metrics_collector,
                                             check_cancelled=check_cancelled))


def prepare_term_translator(settings: SettingsModel, *, metrics_collector=None, check_cancelled=None) -> BaseTranslator | None:
    """Get term-extraction translator instance if configured.

    This translator uses a potentially different engine and separate rate limit
    from the main translation engine.
    """
    translator_config = settings.term_extraction_engine_settings
    if translator_config is None:
        return None

    return probe_translator(_create_translator_instance(
        settings=settings,
        translator_config=translator_config,
        enforce_glossary_support=False,
        term_extraction=True,
        metrics_collector=metrics_collector,
        check_cancelled=check_cancelled,
    ))
