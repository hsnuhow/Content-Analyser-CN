# -*- coding: utf-8 -*-
"""
Email 通知（白名單申請 / 審核通過）

- 透過 Resend transactional API（純 REST，無需 SDK）寄信。
- **旗標 gate**：未設定 RESEND_API_KEY 時整個模組靜默 no-op（不破壞 local／未設定環境）。
- **最佳努力**：任何錯誤只記 log、回傳 False，**絕不向上拋**——寄信失敗不得影響登入/審核流程。
- 使用者 LLM 金鑰等敏感資訊不在此處；本模組只送通知文字。

env：
  RESEND_API_KEY   Resend API 金鑰（Secret Manager 注入；未設 → 通知停用）
  NOTIFY_FROM      寄件人（預設 'InsightOut <notify@annexix.cc>'；需在 Resend 驗證網域）
  SITE_URL         站台網址（預設 'https://insightout.annexix.cc'，用於信中連結）
"""
import os

import requests

RESEND_ENDPOINT = "https://api.resend.com/emails"
_TIMEOUT = 10


def _enabled() -> bool:
    return bool(os.environ.get("RESEND_API_KEY", "").strip())


def _from() -> str:
    return os.environ.get("NOTIFY_FROM", "InsightOut <notify@annexix.cc>")


def _site_url() -> str:
    return os.environ.get("SITE_URL", "https://insightout.annexix.cc").rstrip("/")


def _send(to: str, subject: str, html: str) -> bool:
    """寄一封信。成功回 True；停用或失敗回 False（best-effort，不外拋）。"""
    if not _enabled():
        return False
    to = (to or "").strip()
    if not to:
        return False
    try:
        resp = requests.post(
            RESEND_ENDPOINT,
            headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY'].strip()}",
                     "Content-Type": "application/json"},
            json={"from": _from(), "to": [to], "subject": subject, "html": html},
            timeout=_TIMEOUT,
        )
        if resp.status_code >= 400:
            # 不印出完整回應（可能含信箱）；只記狀態碼便於排查。
            print(f"[Notify] 寄信失敗 to={to} status={resp.status_code}", flush=True)
            return False
        return True
    except Exception as e:
        print(f"[Notify] 寄信例外 to={to}：{e}", flush=True)
        return False


def _esc(s: str) -> str:
    """最小 HTML escape（信件內嵌入使用者提供的姓名/email）。"""
    return (str(s or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def notify_new_application(applicant_email: str, display_name: str, admin_email: str) -> bool:
    """有人首次登入建立 pending → 通知管理員前往後台審核。"""
    if not _enabled() or not (admin_email or "").strip():
        return False
    name = _esc(display_name) or _esc(applicant_email)
    html = (
        f"<p>{name}（{_esc(applicant_email)}）剛申請加入 InsightOut，正在等待審核。</p>"
        f"<p>請至後台審核：<a href=\"{_site_url()}/admin/users\">{_site_url()}/admin/users</a></p>"
    )
    return _send(admin_email, f"【InsightOut】新用戶申請：{applicant_email}", html)


def notify_approved(applicant_email: str, display_name: str) -> bool:
    """審核通過 → 通知申請者可登入使用。"""
    if not _enabled():
        return False
    name = _esc(display_name) or _esc(applicant_email)
    html = (
        f"<p>你好 {name}，</p>"
        f"<p>你的 InsightOut 帳號已通過審核，現在可以登入使用：</p>"
        f"<p><a href=\"{_site_url()}\">{_site_url()}</a></p>"
    )
    return _send(applicant_email, "【InsightOut】你的申請已通過", html)
