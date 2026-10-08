import json
import logging
import os
import subprocess

from pdf2zh_next.config.model import SettingsModel
from pdf2zh_next.translator.base_rate_limiter import BaseRateLimiter
from pdf2zh_next.translator.base_translator import BaseTranslator
from tenacity import before_sleep_log
from tenacity import retry
from tenacity import retry_if_exception_type
from tenacity import stop_after_attempt
from tenacity import wait_exponential

logger = logging.getLogger(__name__)


class ClaudeCodeTranslator(BaseTranslator):
    name = "claudecode"

    def __init__(
        self,
        settings: SettingsModel,
        rate_limiter: BaseRateLimiter,
    ):
        super().__init__(settings, rate_limiter)
        self.claude_code_path = settings.translate_engine_settings.claude_code_path
        self.claude_code_model = settings.translate_engine_settings.claude_code_model
        self.add_cache_impact_parameters("model", self.claude_code_model)
        self.add_cache_impact_parameters("prompt", self.prompt(""))
        self._test_claude_code()

    def _test_claude_code(self):
        try:
            result = subprocess.run(
                [self.claude_code_path, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                raise ValueError(f"Claude Code CLI error: {result.stderr}")
        except FileNotFoundError as e:
            raise ValueError(
                f"Claude Code CLI not found at '{self.claude_code_path}'"
            ) from e

    @retry(
        retry=retry_if_exception_type(
            (subprocess.CalledProcessError, subprocess.TimeoutExpired)
        ),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=15),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def do_translate(self, text, rate_limit_params: dict = None) -> str:
        messages = self.prompt(text)
        input_data = "\n\n".join(message["content"] for message in messages)

        cmd = [
            self.claude_code_path,
            "-p",
            "--model",
            self.claude_code_model,
            "--max-turns",
            "1",
            "--input-format",
            "text",
            "--output-format",
            "json",
            "--tools",
            "",
            "--disallowedTools",
            "mcp__*",
            "--no-session-persistence",
        ]

        env = os.environ.copy()
        env.pop("ANTHROPIC_API_KEY", None)

        try:
            logger.debug("Running Claude Code translation")
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )

            stdout, stderr = process.communicate(input=input_data, timeout=120)

            if process.returncode != 0:
                logger.error(f"Claude Code failed: {stderr}")
                raise subprocess.CalledProcessError(process.returncode, cmd, stderr)

            translation = self._parse_output(stdout)
            return translation.strip()

        except subprocess.TimeoutExpired as e:
            process.kill()
            process.communicate()
            raise ValueError("Claude Code translation timed out") from e

    def _parse_output(self, output: str) -> str:
        try:
            result = json.loads(output)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid JSON result from Claude Code") from exc
        if not isinstance(result, dict) or result.get("type") != "result" or result.get("is_error") or result.get("subtype") != "success":
            raise ValueError("Claude Code did not complete the translation successfully")
        text = result.get("result")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("No translation received from Claude Code")
        return text.strip()
