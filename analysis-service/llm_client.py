# -*- coding: utf-8 -*-
"""
LLM 統一呼叫層

支援 Gemini（google-genai）與 Claude（anthropic）。
呼叫端傳入 provider、model、api_key，由此層統一轉換為對應 SDK 呼叫。
系統不提供 LLM Key，全部由 per-project 設定或呼叫端帶入。
"""
import concurrent.futures
import random
import time

DEFAULT_TEMPERATURE = 0.3
DEFAULT_MAX_TOKENS = 8192
DEFAULT_TOP_P = None  # None = 用各 SDK 預設值
LLM_TIMEOUT_SEC = 300
MAX_RETRIES = 3            # 對 429 / 5xx / overloaded 的重試次數
RETRY_BASE_DELAY = 2.0     # 指數退避基底秒數

SUPPORTED_PROVIDERS = ("gemini", "claude", "openai")


class LLMError(Exception):
    """LLM 呼叫失敗時拋出。"""
    pass


def _is_retryable(exc: Exception) -> bool:
    """暫時性錯誤（rate limit / 過載 / 5xx / 連線）→ 值得退避重試。
    永久性錯誤（invalid key / 模型不存在 / 400）→ 不重試。"""
    m = str(exc).lower()
    retry_markers = ("429", "rate limit", "ratelimit", "resource_exhausted", "quota",
                     "overloaded", "503", "502", "500", "unavailable", "timeout",
                     "timed out", "connection")
    permanent_markers = ("invalid api key", "api key not valid", "permission",
                         "authentication", "401", "403", "not found", "404")
    if any(p in m for p in permanent_markers):
        return False
    return any(r in m for r in retry_markers)


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

        # 對暫時性錯誤（429/5xx/過載）做指數退避重試；並行呼叫（synthesis 4-way、
        # intent 多批）較易撞 rate limit，避免一次 429 就讓整個 job 失敗。
        last_exc = None
        for attempt in range(MAX_RETRIES):
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(_call)
                    return future.result(timeout=LLM_TIMEOUT_SEC)
            except concurrent.futures.TimeoutError as e:
                last_exc = LLMError(f"LLM 呼叫逾時（>{LLM_TIMEOUT_SEC}s，{self.provider}）")
            except Exception as e:
                last_exc = e
                if not _is_retryable(e) or attempt == MAX_RETRIES - 1:
                    break
                delay = RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
                print(f"[LLMClient] {self.provider} 暫時性錯誤，{delay:.1f}s 後重試"
                      f"（{attempt + 1}/{MAX_RETRIES}）：{e}", flush=True)
                time.sleep(delay)
                continue
        if isinstance(last_exc, LLMError):
            raise last_exc
        raise LLMError(f"LLM 呼叫失敗（{self.provider}）：{last_exc}") from last_exc

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
            # 僅在「模型本身有問題」時退回 flash；rate-limit/quota/auth 不退回
            # （那類退回也會再失敗，浪費一次呼叫），交給外層退避重試/錯誤處理。
            fallback = "gemini-2.5-flash"
            m = str(e).lower()
            model_issue = any(k in m for k in (
                "not found", "404", "not supported", "unsupported", "does not exist", "invalid model"))
            transient_or_auth = any(k in m for k in ("rate", "quota", "429", "401", "403", "permission"))
            if self.model != fallback and model_issue and not transient_or_auth:
                print(f"[LLMClient] 模型 {self.model} 不可用，後備使用 {fallback}: {e}", flush=True)
                return _generate(fallback)
            raise

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
        # 防呆：content 可能為空或含非 text block（如 max_tokens 截斷、tool block）。
        txt = "".join(getattr(b, "text", "") for b in (msg.content or [])
                      if getattr(b, "type", "") == "text")
        return txt.strip()

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
