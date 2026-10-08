import logging
from urllib.parse import urlsplit

import httpx

from azure.ai.translation.text import TextTranslationClient
from azure.core.credentials import AzureKeyCredential
from pdf2zh_next.config.model import SettingsModel
from pdf2zh_next.translator.base_rate_limiter import BaseRateLimiter
from pdf2zh_next.translator.base_translator import BaseTranslator
from tenacity import before_sleep_log
from tenacity import retry
from tenacity import retry_if_exception_type
from tenacity import stop_after_attempt
from tenacity import wait_exponential

logger = logging.getLogger(__name__)


class AzureTranslator(BaseTranslator):
    name = "azure"
    lang_map = {"zh": "zh-Hans", "zh-cn": "zh-Hans", "zh-tw": "zh-Hant"}

    def __init__(
        self,
        settings: SettingsModel,
        rate_limiter: BaseRateLimiter,
    ):
        super().__init__(settings, rate_limiter)
        endpoint = settings.translate_engine_settings.azure_endpoint
        api_key = settings.translate_engine_settings.azure_api_key
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.region = settings.translate_engine_settings.azure_region
        host = urlsplit(endpoint).hostname or ""
        # Sovereign-cloud documentation still specifies the v3 wire format;
        # SDK 2.x only implements the new dated API and cannot emit v3 requests.
        self.sovereign = host.endswith(".azure.cn") or host.endswith(".microsofttranslator.us")
        if self.sovereign:
            self.client = httpx.Client(timeout=60)
            return
        credential = AzureKeyCredential(api_key)
        self.client = TextTranslationClient(
            endpoint=endpoint, credential=credential, region=settings.translate_engine_settings.azure_region or None
        )

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def do_translate(self, text, rate_limit_params: dict = None):
        if self.sovereign:
            headers = {"Ocp-Apim-Subscription-Key": self.api_key}
            if self.region:
                headers["Ocp-Apim-Subscription-Region"] = self.region
            params = {"api-version": "3.0", "to": self.lang_out}
            if self.lang_in != "auto":
                params["from"] = self.lang_in
            endpoint = self.endpoint if self.endpoint.endswith("/translate") else self.endpoint + "/translate"
            response = self.client.post(endpoint, params=params, headers=headers, json=[{"Text": text}])
            response.raise_for_status()
            return response.json()[0]["translations"][0]["text"]
        response = self.client.translate(
            body=[text],
            from_language=None if self.lang_in == "auto" else self.lang_in,
            to_language=[self.lang_out],
        )
        translated_text = response[0].translations[0].text
        return translated_text
