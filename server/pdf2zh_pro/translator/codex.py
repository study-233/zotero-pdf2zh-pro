"""Codex translation adapter; the process client is shared, not owned here."""
import threading

from codex_client import CodexError, get_codex_client
from pdf2zh_pro.translator.account import AccountTranslator


class CodexTaskState:
    """Stop new calls for this task, without cancelling its in-flight work."""
    def __init__(self):
        self.lock = threading.Lock()
        self.error = None

    def check(self):
        with self.lock:
            if self.error is not None:
                raise CodexError(*self.error)

    def stop_for(self, error):
        if isinstance(error, CodexError) and error.code in {
            "codex_quota_exhausted", "codex_not_logged_in", "codex_model_unavailable",
            "codex_invalid_reasoning", "codex_isolation_failed", "codex_incompatible",
            "codex_not_installed",
        }:
            with self.lock:
                if self.error is None:
                    self.error = (error.code, error.public_message, error.status_code)


class CodexTranslator(AccountTranslator):
    default_protocol = "codex_app_server"
    name = "codex"
    limits_each_attempt = True
    recommended_qps = 2
    recommended_pool_max_workers = 2

    def __init__(self, settings, rate_limiter):
        super().__init__(settings, rate_limiter)
        engine = settings.translate_engine_settings
        self.model = engine.codex_model
        self.reasoning_effort = engine.codex_reasoning_effort
        self.timeout = engine.codex_timeout
        self.client = get_codex_client(engine.codex_cli_path,
            proxy_mode=engine.codex_proxy_mode, proxy_url=engine.codex_proxy_url).session()
        self.check_cancelled = lambda: None
        self._request_slots = threading.BoundedSemaphore(2)
        self.request_state = CodexTaskState()
        self.configure_cache_namespace(provider="codex")
        self.add_cache_impact_parameters("model", self.model)
        self.add_cache_impact_parameters("reasoning_effort", self.reasoning_effort)
        self.add_cache_impact_parameters("prompt_template_version", 1)
        self.add_cache_fingerprint("prompt_fingerprint", self.prompt(""))
        self.add_cache_fingerprint("custom_system_prompt_fingerprint", settings.translation.custom_system_prompt)

