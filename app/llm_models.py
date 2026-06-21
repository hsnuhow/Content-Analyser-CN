# -*- coding: utf-8 -*-
"""LLM 供應商可用模型查詢（自 project_routes.py 抽出）。

單一職責：用使用者的 API key 即時抓 gemini / openai / claude 的可用模型清單（REST，不依賴
SDK）。金鑰一律走 header（不放 URL query），避免網路例外字串夾帶含金鑰的完整 URL 落入 log。
無 db / Flask 依賴，可單獨 import（測試以 mock requests 驗解析/過濾邏輯）。
"""
import requests


def _fetch_provider_models(provider: str, api_key: str) -> list:
    """用 API key 即時抓取各家可用模型清單（REST，不依賴 SDK）。失敗回傳 []。"""
    provider = (provider or '').lower().strip()
    if not api_key:
        return []
    try:
        if provider == 'gemini':
            # 金鑰以 header（x-goog-api-key）傳送，不放 URL query：
            # 避免網路例外字串夾帶含金鑰的完整 URL 而落入 Cloud Run log。
            r = requests.get(
                'https://generativelanguage.googleapis.com/v1beta/models',
                headers={'x-goog-api-key': api_key}, timeout=10)
            if r.status_code != 200:
                return []
            out = []
            for m in r.json().get('models', []):
                methods = m.get('supportedGenerationMethods', [])
                if 'generateContent' in methods:
                    out.append(m.get('name', '').replace('models/', ''))
            return [x for x in out if x]
        elif provider == 'openai':
            r = requests.get('https://api.openai.com/v1/models',
                             headers={'Authorization': f'Bearer {api_key}'}, timeout=10)
            if r.status_code != 200:
                return []
            ids = [d.get('id', '') for d in r.json().get('data', [])]
            # 只留對話型模型（gpt / o 系列 / chatgpt）
            return sorted(i for i in ids if i and (i.startswith('gpt') or i.startswith('o') or i.startswith('chatgpt')))
        elif provider == 'claude':
            r = requests.get('https://api.anthropic.com/v1/models',
                             headers={'x-api-key': api_key,
                                      'anthropic-version': '2023-06-01'}, timeout=10)
            if r.status_code != 200:
                return []
            return [d.get('id', '') for d in r.json().get('data', []) if d.get('id')]
    except Exception as e:
        print(f"[models] {provider} 抓取失敗：{e}", flush=True)
    return []
