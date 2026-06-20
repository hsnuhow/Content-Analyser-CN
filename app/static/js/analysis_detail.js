// 分析報告頁：進度輪詢 / 延伸報告輪詢 / Markdown 渲染。
// 自 analysis_detail.html 的 3 段 inline <script> 抽出（CSP 強化）。
// 各段以「元素是否存在」自我守衛（三種狀態互斥），URL 由 data-* 帶入、
// Markdown 由 <script type="application/json"> 資料島帶入。可無條件載入。
(function () {
  // 1) 分析進度輪詢（pending/running 時）
  var statusEl = document.getElementById('status-log');
  if (statusEl && statusEl.getAttribute('data-status-url')) {
    var statusUrl = statusEl.getAttribute('data-status-url');
    (function poll(attempts) {
      if (attempts >= 200) { statusEl.textContent = '⚠️ 輪詢逾時，請重新整理頁面確認狀態。'; return; }
      fetch(statusUrl).then(function (r) { return r.json(); }).then(function (data) {
        var bar = document.getElementById('progress-bar');
        var pct = document.getElementById('progress-pct');
        if (bar) bar.style.width = (data.progress || 0) + '%';
        if (pct) pct.textContent = (data.progress || 0) + '%';
        statusEl.textContent = data.log || '';
        if (data.status === 'completed' || data.status === 'failed') { location.reload(); }
        else { setTimeout(function () { poll(attempts + 1); }, 3000); }
      }).catch(function () { setTimeout(function () { poll(attempts + 1); }, 5000); });
    })(0);
  }

  // 2) 延伸報告產生輪詢（derive running 時）
  var dlog = document.getElementById('derive-log');
  if (dlog && dlog.getAttribute('data-derive-status-url')) {
    var deriveUrl = dlog.getAttribute('data-derive-status-url');
    (function dpoll(n) {
      if (n >= 300) { dlog.textContent = '仍在產生中，請稍後重新整理頁面查看結果。'; return; }
      fetch(deriveUrl).then(function (r) { return r.json(); }).then(function (d) {
        if (d.status === 'completed' || d.status === 'failed') { location.reload(); }
        else { if (d.log) dlog.textContent = d.log; setTimeout(function () { dpoll(n + 1); }, 3000); }
      }).catch(function () { setTimeout(function () { dpoll(n + 1); }, 5000); });
    })(0);
  }

  // 3) Markdown 報告渲染（completed 時；marked/DOMPurify 由頁面 CDN 先載入）
  var out = document.getElementById('markdown-output');
  var mdEl = document.getElementById('report-md');
  if (out && mdEl) {
    var md = '';
    try { md = JSON.parse(mdEl.textContent); } catch (e) { md = mdEl.textContent || ''; }
    out.innerHTML = DOMPurify.sanitize(marked.parse(md));
  }
})();
