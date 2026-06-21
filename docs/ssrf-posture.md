# SSRF 防護現況與決策紀錄（crawler-service）

> 建立：2026-06-21。**結論：真正高風險的 metadata SSRF 已由 app 層修正涵蓋；打內網服務的風險在現行架構下幾乎不存在，目前不需任何網路層（VPC/防火牆）變更。** 本文件供日後架構變動時回頭對照。

## 什麼是 SSRF（打內網）

爬蟲是「住在 Google 資料中心的跑腿工」。你叫他去**外部**網址抄文章；SSRF 是有人給一個**動過手腳的網址**（表面是外站、實際解析到內部位址，或先回公網再 302 轉址到內部），騙他**不出大樓**、去敲只有自己人能碰的內部門，把內容抄回給攻擊者。

內部可被敲的目標只有兩類：

### ① metadata server（169.254.169.254）— 萬能鑰匙電話【真正危險】

每台 GCP 機器內建的內線，打了會給 Service Account token（能開 Firestore、Secret Manager 的萬能鑰匙）。被 SSRF 打到 = 洩漏系統萬能鑰匙。

**現況：✅ 已修（app 層）。** `crawler-service/net_guard.is_safe_ip` + `crawler.py` 的 `_assert_safe_remote_ip`：`driver.get` 後用 Chrome performance log 取「主文件實際連到的 remoteIPAddress」再驗一次，命中內網（含 metadata）即拋 `UnsupportedSiteError` 丟棄結果。涵蓋 scrape / extract-images / research 三端點，fail-open 不誤殺合法站。

**為什麼不在網路層封 metadata：** Cloud Run 服務**自己**要靠 169.254.169.254 拿 SA token 才能存取 Firestore/Secret。在 egress 封掉 = app 自己也斷、四服務全掛。且 metadata 是 link-local，走 instance 本地、**不經 VPC egress**，網路層也擋不到。網路上**無法**區分「Chrome 被騙的 metadata 請求」與「app SDK 正常的 token 請求」（同 instance、同出口）→ 故 **app 層才是對的防護層**，沒有對應的安全 gcloud 指令。

### ② 自架私有內網服務（10.x / 172.16 / 192.168）【現況近乎不存在】

指自己在內網架、用私有 IP 跑、不對外的伺服器（內部 DB、後台機、內部 API）。

**現況：本專案沒有這種東西。** 四個 Cloud Run 服務彼此用**公開 HTTPS 網址 + 金鑰**互打；Firestore / Secret Manager 都是 Google **公開 API 端點 + 金鑰**。無任何「私有 IP 內網服務」。所以這類 SSRF **沒有可打的目標**，風險很低。

## 決策：目前不做網路層變更

唯一的網路層選項是「VPC connector + 防火牆封 RFC1918 內網段」，但它只擋 **②**（內網服務），擋不到 **①**（metadata）。而本專案的 ② 幾乎是空的，等於**花大力氣鎖一個沒放東西的房間**，且設錯會讓**所有爬取斷線**（爬蟲本職就是連外）。依 CLAUDE.md §7.1 基礎設施變更需人工執行。

**結論：不需要做。** 真正的風險（①）已處理；② 在現行架構下不存在。

## 何時要回頭重新評估（觸發條件）

只要出現下列任一情況，重看 ②（考慮 VPC + RFC1918 防火牆）：

- 開始自架**內網私有服務**（私有 IP 的 DB、Redis、內部 API、admin 後台），且與 crawler 同 VPC/專案可達。
- 服務間改用**私有 IP / VPC 內部互連**（而非現在的公開 HTTPS + 金鑰）。
- 接上**內部公司網路**（VPN / Interconnect / 對等互連），讓 Cloud Run 能觸及公司內網。

## 對照

- 程式修正：見 `changelog.md`「2026-06-21 安全：crawler-service 漏洞審查 + 三項修補」。
- 相關：`crawler-service/net_guard.py`（`is_safe_url` 逐跳 + `is_safe_ip`）、`crawler-service/crawler.py`（`_assert_safe_remote_ip`）、`tests/test_net_guard.py`。
