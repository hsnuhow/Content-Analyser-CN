# -*- coding: utf-8 -*-
"""
LLM 統一呼叫層

支援 Gemini（google-genai）與 Claude（anthropic）。
呼叫端傳入 provider、model、api_key，由此層統一轉換為對應 SDK 呼叫。
系統不提供 LLM Key，全部由 per-project 設定或呼叫端帶入。
"""
import concurrent.futures

DEFAULT_TEMPERATURE = 0.3
DEFAULT_MAX_TOKENS = 8192
LLM_TIMEOUT_SEC = 300

SUPPORTED_PROVIDERS = ("gemini", "claude")


class LLMError(Exception):
    """LLM 呼叫失敗時拋出。"""
    pass


class LLMClient:
    def __init__(self, provider: str, model: str, api_key: str):
        provider = provider.lower().strip()
        if provider not in SUPPORTED_PROVIDERS:
            raise ValueError(f"不支援的 LLM 提供商：'{provider}'。請使用 'gemini' 或 'claude'。")
        if not api_key or not api_key.strip():
            raise ValueError("api_key 不可為空。")

        self.provider = provider
        self.model = model
        self.api_key = api_key.strip()

    def generate(self, prompt: str,
                 temperature: float = DEFAULT_TEMPERATURE,
                 max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
        """呼叫 LLM，回傳生成的文字。失敗或逾時時拋出 LLMError。"""
        def _call():
            if self.provider == "gemini":
                return self._call_gemini(prompt, temperature, max_tokens)
            elif self.provider == "claude":
                return self._call_claude(prompt, temperature, max_tokens)

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_call)
                return future.result(timeout=LLM_TIMEOUT_SEC)
        except concurrent.futures.TimeoutError:
            raise LLMError(f"LLM 呼叫逾時（>{LLM_TIMEOUT_SEC}s，{self.provider}）")
        except LLMError:
            raise
        except Exception as e:
            raise LLMError(f"LLM 呼叫失敗（{self.provider}）：{e}") from e

    def _call_gemini(self, prompt: str, temperature: float, max_tokens: int) -> str:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)

        def _generate(model_name):
            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                ),
            )
            return (resp.text or "").strip()

        try:
            return _generate(self.model)
        except Exception as e:
            fallback = "gemini-2.5-flash"
            if self.model != fallback:
                print(f"[LLMClient] {self.model} 失敗，後備使用 {fallback}: {e}", flush=True)
                return _generate(fallback)
            raise LLMError(f"Gemini 呼叫失敗：{e}") from e

    def _call_claude(self, prompt: str, temperature: float, max_tokens: int) -> str:
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key)
        msg = client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        return (msg.content[0].text or "").strip()
