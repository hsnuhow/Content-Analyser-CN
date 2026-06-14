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
DEFAULT_TOP_P = None  # None = 用各 SDK 預設值
LLM_TIMEOUT_SEC = 300

SUPPORTED_PROVIDERS = ("gemini", "claude", "openai")


class LLMError(Exception):
    """LLM 呼叫失敗時拋出。"""
    pass


class LLMClient:
    def __init__(self, provider: str, model: str, api_key: str,
                 temperature: float = DEFAULT_TEMPERATURE,
                 thinking: bool = False,
                 max_tokens: int = DEFAULT_MAX_TOKENS,
                 top_p: float = DEFAULT_TOP_P):
        provider = provider.lower().strip()
        if provider not in SUPPORTED_PROVIDERS:
            raise ValueError(f"不支援的 LLM 提供商：'{provider}'。請使用 'gemini'、'claude' 或 'openai'。")
        if not api_key or not api_key.strip():
            raise ValueError("api_key 不可為空。")

        self.provider = provider
        self.model = model
        self.api_key = api_key.strip()
        # 用戶可調：溫度、Gemini 2.5 thinking 開關、輸出長度上限、top_p。
        self.temperature = temperature
        self.thinking = bool(thinking)
        self.max_tokens = max_tokens or DEFAULT_MAX_TOKENS
        self.top_p = top_p

    def generate(self, prompt: str,
                 temperature: float = None,
                 max_tokens: int = None,
                 top_p: float = None) -> str:
        """呼叫 LLM，回傳生成的文字。失敗或逾時時拋出 LLMError。"""
        if temperature is None:
            temperature = self.temperature      # 用戶設定的預設溫度
        if max_tokens is None:
            max_tokens = self.max_tokens         # 用戶設定的輸出長度上限
        if top_p is None:
            top_p = self.top_p
        def _call():
            if self.provider == "gemini":
                return self._call_gemini(prompt, temperature, max_tokens, top_p)
            elif self.provider == "claude":
                return self._call_claude(prompt, temperature, max_tokens, top_p)
            elif self.provider == "openai":
                return self._call_openai(prompt, temperature, max_tokens, top_p)

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

    def _call_gemini(self, prompt: str, temperature: float, max_tokens: int,
                     top_p: float = None) -> str:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)

        def _generate(model_name):
            cfg_kwargs = dict(
                temperature=temperature,
                max_output_tokens=max_tokens,
            )
            if top_p is not None:
                cfg_kwargs["top_p"] = top_p
            # ⭐ Gemini 2.5 預設開啟 thinking，思考 token 吃掉 max_output_tokens → 輸出被截斷。
            #   預設關閉（budget=0）全部預算給輸出；用戶可在專案設定開啟 thinking（self.thinking）。
            if "2.5" in model_name and not self.thinking:
                try:
                    cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
                except Exception:
                    pass
            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(**cfg_kwargs),
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

    def _call_claude(self, prompt: str, temperature: float, max_tokens: int,
                     top_p: float = None) -> str:
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key)
        kwargs = dict(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        if top_p is not None:
            kwargs["top_p"] = top_p
        msg = client.messages.create(**kwargs)
        return (msg.content[0].text or "").strip()

    def _call_openai(self, prompt: str, temperature: float, max_tokens: int,
                     top_p: float = None) -> str:
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key)
        kwargs = dict(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if top_p is not None:
            kwargs["top_p"] = top_p
        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception as e:
            # 部分新模型（o 系列）不接受 max_tokens/temperature，改用相容參數重試。
            msg = str(e).lower()
            if "max_tokens" in msg or "temperature" in msg or "unsupported" in msg:
                alt = dict(model=self.model,
                           messages=[{"role": "user", "content": prompt}],
                           max_completion_tokens=max_tokens)
                resp = client.chat.completions.create(**alt)
            else:
                raise
        return (resp.choices[0].message.content or "").strip()
