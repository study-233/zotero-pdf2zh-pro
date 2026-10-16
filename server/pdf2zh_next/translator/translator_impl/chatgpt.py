"""ChatGPT plan provider using the same PDF translation contract as other engines."""
import threading

from chatgpt_client import ChatGPTError, get_chatgpt_client
from pdf2zh_next.translator.account_translator import AccountTranslator


class ChatGPTTaskState:
    def __init__(self):
        self.lock, self.error = threading.Lock(), None

    def check(self):
        with self.lock:
            if self.error:
                raise ChatGPTError(*self.error, stop_task=True)

    def stop_for(self, error):
        if isinstance(error, ChatGPTError) and error.stop_task:
            with self.lock:
                if self.error is None:
                    self.error = (error.code, error.public_message, error.status_code)


class ChatGPTTranslator(AccountTranslator):
    name = 'chatgpt'
    pdf2zh_next_recommended_qps = 3
    pdf2zh_next_recommended_pool_max_workers = 24

    def __init__(self, settings, rate_limiter):
        super().__init__(settings, rate_limiter)
        engine = settings.translate_engine_settings
        self.model = engine.chatgpt_model
        self.reasoning_effort = engine.chatgpt_reasoning_effort
        self.timeout = engine.chatgpt_timeout
        self.client = get_chatgpt_client(engine.chatgpt_account_id)
        self.check_cancelled = lambda: None
        self._request_slots = threading.BoundedSemaphore(self.pdf2zh_next_recommended_pool_max_workers)
        self.request_state = ChatGPTTaskState()
        self.configure_cache_namespace(provider='chatgpt')
        self.add_cache_impact_parameters('model', self.model)
        self.add_cache_impact_parameters('reasoning_effort', self.reasoning_effort)
        self.add_cache_impact_parameters('prompt_template_version', 1)
        self.add_cache_fingerprint('prompt_fingerprint', self.prompt(''))
        self.add_cache_fingerprint('custom_system_prompt_fingerprint', settings.translation.custom_system_prompt)
