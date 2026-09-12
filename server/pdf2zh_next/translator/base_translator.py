import contextlib
import hashlib
import json
import logging
import re
from babeldoc.translator.validation import InvalidTranslation

from abc import ABC
from abc import abstractmethod

from pdf2zh_next.config.model import SettingsModel
from pdf2zh_next.translator.base_rate_limiter import BaseRateLimiter
from pdf2zh_next.translator.cache import TranslationCache

logger = logging.getLogger(__name__)


class BaseTranslator(ABC):
    # Due to cache limitations, name should be within 20 characters.
    # cache.py: translate_engine = CharField(max_length=20)
    """translator 的基类，所有的 translator 的实现都需要继承"""

    name = "base"
    lang_map = {}

    def __init__(
        self,
        settings: SettingsModel,
        rate_limiter: BaseRateLimiter,
    ):
        """
        translator class initialization
        :param settings: runtime setting and configuration
        :param rate_limiter: LLM request rate control
        :return: None
        """
        self.ignore_cache = settings.translation.ignore_cache
        lang_in = self.lang_map.get(
            settings.translation.lang_in.lower(), settings.translation.lang_in
        )
        lang_out = self.lang_map.get(
            settings.translation.lang_out.lower(), settings.translation.lang_out
        )
        self.lang_in = lang_in
        self.lang_out = lang_out
        self.rate_limiter = rate_limiter

        self.cache = TranslationCache(
            self.name,
            {
                "cache_schema": 2,
                "lang_in": lang_in,
                "lang_out": lang_out,
            },
        )

        self.translate_call_count = 0
        self.translate_cache_call_count = 0
        self.metrics_collector = None

    def __del__(self):
        with contextlib.suppress(Exception):
            logger.info(
                f"{self.name} translate call count: {self.translate_call_count}"
            )
            logger.info(
                f"{self.name} translate cache call count: {self.translate_cache_call_count}",
            )

    def add_cache_impact_parameters(self, k: str, v):
        """
        Add parameters that affect the translation quality to distinguish the translation effects under different parameters.
        :param k: key
        :param v: value
        """
        self.cache.add_params(k, v)

    def add_cache_fingerprint(self, key: str, value) -> None:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self.add_cache_impact_parameters(
            key,
            hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        )

    def set_metrics_collector(self, collector) -> None:
        self.metrics_collector = collector

    def configure_cache_namespace(self, *, provider: str) -> None:
        self.add_cache_impact_parameters("provider", provider)

    def _commit_validated_cache(self, text, output, rate_limit_params=None, *, ignore_cache=False):
        """Commit a settled batch without issuing a request or counting an attempt."""
        params = rate_limit_params or {}
        if self.ignore_cache or ignore_cache or params.get("ignore_cache", False):
            return False
        check = params.get("check_cancelled") or getattr(self, "check_cancelled", None)
        if check:
            check()
        try:
            params.get("validate_output", lambda value: None)(output)
        except InvalidTranslation:
            return False
        if not params.get("cache_output_if", lambda value: True)(output):
            return False
        if check:
            check()
        try:
            self.cache.set(text, output)
        except Exception as error:
            logger.debug("translation cache commit failed: error_type=%s", type(error).__name__)
            return False
        return True

    @staticmethod
    def _review_cache_key(identity):
        value = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "quality-review:" + hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _get_review_cache(self, identity, *, ignore_cache=False):
        if self.ignore_cache or ignore_cache:
            return None
        try:
            value = self.cache.get(self._review_cache_key(identity))
        except Exception as error:
            logger.debug("review cache lookup failed: error_type=%s", type(error).__name__)
            return None
        return value if isinstance(value, str) and value.strip() else None

    def _set_review_cache(self, identity, output, *, ignore_cache=False, check_cancelled=None):
        if not isinstance(output, str) or not output.strip():
            return False
        return self._commit_validated_cache(
            self._review_cache_key(identity), output,
            {"check_cancelled": check_cancelled}, ignore_cache=ignore_cache,
        )

    def _translate_cached(self, text, ignore_cache, rate_limit_params, method):
        self.translate_call_count += 1
        params = rate_limit_params or {}
        validate = params.get("validate_output", lambda value: None)
        cache_output_if = params.get("cache_output_if", lambda value: True)
        use_cache = not (self.ignore_cache or ignore_cache)
        if use_cache:
            try:
                cached = self.cache.get(text)
            except Exception as error:
                logger.debug("translation cache lookup failed: error_type=%s", type(error).__name__)
                cached = None
            if cached is not None:
                try:
                    validate(cached)
                except InvalidTranslation:
                    with contextlib.suppress(Exception):
                        self.cache.delete(text)
                else:
                    if not cache_output_if(cached):
                        # A structurally valid batch can still contain useful
                        # translations; let its caller repair just the bad rows.
                        with contextlib.suppress(Exception):
                            self.cache.delete(text)
                    self.translate_cache_call_count += 1
                    if self.metrics_collector is not None:
                        self.metrics_collector.local_cache_hit()
                    return cached
        if self.metrics_collector is not None:
            self.metrics_collector.local_cache_miss()
        for generation in range(2):
            if not getattr(self, "limits_each_attempt", False):
                self.rate_limiter.wait(params)
                if params.get("on_attempt"):
                    params["on_attempt"]()
            try:
                translation = method(text, rate_limit_params)
                validate(translation)
            except InvalidTranslation:
                if generation:
                    raise
                if self.metrics_collector is not None:
                    self.metrics_collector.retry_scheduled(kind=params.get("metric_kind", "translation"))
                continue
            if use_cache and not params.get("defer_cache_write", False):
                self._commit_validated_cache(text, translation, params)
            return translation

    def translate(self, text, ignore_cache=False, rate_limit_params=None):
        return self._translate_cached(text, ignore_cache, rate_limit_params, self.do_translate)

    def llm_translate(self, text, ignore_cache=False, rate_limit_params=None):
        return self._translate_cached(text, ignore_cache, rate_limit_params, self.do_llm_translate)

    def do_llm_translate(self, text, rate_limit_params: dict = None):
        """
        Actual translate text, override this method
        :param text: text to translate
        :return: translated text
        """
        raise NotImplementedError

    @abstractmethod
    def do_translate(self, text, rate_limit_params: dict = None):
        """
        Actual translate text, override this method
        :param text: text to translate
        :return: translated text
        """
        logger.critical(
            f"Do not call BaseTranslator.do_translate. "
            f"Translator: {self}. "
            f"Text: {text}. ",
        )
        raise NotImplementedError

    def _remove_cot_content(self, content: str) -> str:
        """Remove text content with the thought chain from the chat response

        :param content: Non-streaming text content
        :return: Text without a thought chain
        """
        return re.sub(r"^<think>.+?</think>", "", content, count=1, flags=re.DOTALL)

    def __str__(self):
        """
        get translator's info
        """
        return f"{self.name} {self.lang_in} {self.lang_out} {self.model}"

    def get_formular_placeholder(self, placeholder_id: int):
        """
        get formular placeholder
        LLM translator use placeholder to skip the formular char
        :param placeholder_id: placeholder id
        :return formated placeholder and regex placeholder
        """
        return "{v" + str(placeholder_id) + "}", f"{{\\s*v\\s*{placeholder_id}\\s*}}"

    def get_rich_text_left_placeholder(self, placeholder_id: int):
        """
        get rich text placeholder
        :param placeholder_id: placeholder id
        :return the start label of rich text and regex start label
        """
        return (
            f"<style id='{placeholder_id}'>",
            f"<\\s*style\\s*id\\s*=\\s*'\\s*{placeholder_id}\\s*'\\s*>",
        )

    def get_rich_text_right_placeholder(self, placeholder_id: int):
        """
        get rich text placeholder
        :return the end label of rich text and regex end label
        """
        return "</style>", r"<\s*\/\s*style\s*>"

    def prompt(self, text):
        """
        concatent the prompt
        :param text: input text
        :return: the whole prompt for LLM translator
        """
        return [
            {
                "role": "user",
                "content": f"You are a professional,authentic machine translation engine.\n\n;; Treat next line as plain text input and translate it into {self.lang_out}, output translation ONLY. If translation is unnecessary (e.g. proper nouns, codes, {'{{1}}, etc. '}), return the original text. NO explanations. NO notes. Input:\n\n{text}",
            },
        ]
