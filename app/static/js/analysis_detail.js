// 分析報告頁：進度輪詢 / 延伸報告輪詢 / Markdown 渲染。
// 自 analysis_detail.html 的 3 段 inline <script> 抽出（CSP 強化）。
// 輪詢與 Markdown 渲染都走 app.js 的共用 window.poll / window.renderMarkdown
//（內建嘗試上限與 CDN 守衛）。在 DOMContentLoaded 後執行，確保 app.js 已先定義好它們。
// 三段各以「元素是否存在」自我守衛（三種狀態互斥，模板只渲染對應狀態的元素）。
document.addEventListener('DOMContentLoaded', function () {
  // 1) 分析進度輪詢（pending/running 時才由模板渲染 #status-log[data-status-url]）
  var statusEl = document.getElementById('status-log');
  if (statusEl && statusEl.getAttribute('data-status-url')) {
    poll(statusEl.getAttribute('data-status-url'), {
      interval: 3000, maxAttempts: 200,   // 約 10 分鐘上限
      onData: function (data) {
        var bar = document.getElementById('progress-bar');
        var pct = document.getElementById('progress-pct');
        if (bar) bar.style.width = (data.progress || 0) + '%';
        if (pct) pct.textContent = (data.progress || 0) + '%';
        statusEl.textContent = data.log || '';
        // 完成/失敗 → 重新載入讓伺服器渲染最終報告（屆時模板不再渲染此輪詢元素，不會再輪詢）
        return (data.status === 'completed' || data.status === 'failed') ? 'reload' : 'continue';
      },
      onTimeout: function () { statusEl.textContent = '⚠️ 輪詢逾時，請重新整理頁面確認狀態。'; }
    });
  }

  // 2) 延伸報告產生輪詢（derive running 時）
  var dlog = document.getElementById('derive-log');
  if (dlog && dlog.getAttribute('data-derive-status-url')) {
    poll(dlog.getAttribute('data-derive-status-url'), {
      interval: 3000, maxAttempts: 300,   // 延伸報告較久，約 15 分鐘上限
      onData: function (d) {
        if (d.status === 'completed' || d.status === 'failed') return 'reload';
        if (d.log) dlog.textContent = d.log;
        return 'continue';
      },
      onTimeout: function () { dlog.textContent = '仍在產生中，請稍後重新整理頁面查看結果。'; }
    });
  }

  // 3) Markdown 報告渲染（completed 時；marked/DOMPurify 由頁面 CDN 先載入）
  renderMarkdown(document.getElementById('markdown-output'), document.getElementById('report-md'));
});
