// 選擇器研究候選頁：主動研究 job 的進度輪詢。
// 自 admin_selector_candidates.html 的 inline <script> 抽出（CSP 強化）。
// 狀態查詢 URL 由 #arResult 的 data-status-url 帶入（Jinja 渲染）；
// 無 #arResult（無進行中的研究 job）則不啟動，故此檔可無條件載入。
// 輪詢走 app.js 的共用 window.poll（內建嘗試上限）；在 DOMContentLoaded 後執行，
// 確保 app.js 已先定義好 window.poll。
(function () {
  function init() {
    var root = document.getElementById('arResult');
    if (!root) return;
    var statusUrl = root.getAttribute('data-status-url');
    if (!statusUrl) return;

    function esc(s) {
      return String(s || '').replace(/[&<>"']/g, function (c) {
        return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
      });
    }

    poll(statusUrl, {
      interval: 4000, errorInterval: 6000, maxAttempts: 150,   // 約 10 分鐘上限
      onData: function (d, ctx) {
        document.getElementById('arLog').textContent = d.log || d.status || '';
        if (d.status === 'completed') {
          var res = d.result || {};
          var h = '';
          (res.candidates || []).forEach(function (c) {
            h += '<div class="alert alert-success py-2 mb-2"><b>' + esc(c.domain) + '</b> → 候選 <code>' + esc(c.selector) + '</code>（' + (c.validated_chars || 0) + ' 字）— 下方可確認升級</div>';
          });
          (res.diagnoses || []).forEach(function (g) {
            h += '<div class="alert alert-warning py-2 mb-2"><b>' + esc(g.domain) + '</b>：' + esc(g.diagnosis) + '</div>';
          });
          document.getElementById('arResult').innerHTML = h || '<div class="text-muted">無候選/診斷。</div>';
          // 只有確實觀察到「進行中→完成」的轉換才 reload（刷新含確認按鈕的候選列表）；
          // 若一進頁面就是 completed（session 殘留指向舊 job），不 reload → 杜絕無限重整。
          return ctx.sawInProgress ? 'reload' : 'stop';
        }
        if (d.status === 'failed') {
          document.getElementById('arLog').textContent = '研究失敗：' + (d.log || '');
          return 'stop';
        }
        if (d.status === 'none') {
          document.getElementById('arLog').textContent = '';
          return 'stop';
        }
        ctx.sawInProgress = true;   // 研究進行中 → 之後若轉完成才 reload
        return 'continue';
      }
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
