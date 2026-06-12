# -*- coding: utf-8 -*-
"""
Worker 模組（Phase 0 清理版）

Phase 0：移除舊有的 CRAWLER_LOCK 與 analysis_pipeline()。
  - CRAWLER_LOCK 在微服務架構下無意義（全域鎖阻擋所有用戶）
  - analysis_pipeline() 的爬蟲協調邏輯已移除（主程式不再協調爬蟲）

Phase 3 將重建：
  - 新的任務提交流程呼叫 analysis-pipeline 服務（HTTP API）
  - 任務狀態改由 Firestore projects/{id}/analyses/{id} 管理
"""
