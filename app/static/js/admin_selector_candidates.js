// 選擇器研究候選頁：主動研究 job 的進度輪詢。
// 自 admin_selector_candidates.html 的 inline <script> 抽出（CSP 強化）。
// 狀態查詢 URL 由 #arResult 的 data-status-url 帶入（Jinja 渲染）；
// 無 #arResult（無進行中的研究 job）則不啟動，故此檔可無條件載入。
(function () {
  var root = document.getElementById('arResult');
  if (!root) return;
  var statusUrl = root.getAttribute('data-status-url');
  if (!statusUrl) return;
  // 只有「本次真的觀察到 進行中→完成 的轉換」才 reload 刷新候選列表；
  // 若一進頁面就是 completed（舊的研究 job，session 殘留），不 reload → 防無限重整。
  var sawInProgress = false;

  function esc(s) {
    return String(s || '').replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
    });
  }

  function pollAR() {
    fetch(statusUrl).then(function (r) { return r.json(); }).then(function (d) {
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
        if (sawInProgress) {
          // 確實看到研究跑完 → reload 一次，讓伺服器重新渲染含新候選的列表（有確認/拒絕按鈕）。
          setTimeout(function () { location.reload(); }, 1500);
        }
      } else if (d.status === 'failed') {
        document.getElementById('arLog').textContent = '研究失敗：' + (d.log || '');
      } else if (d.status === 'none') {
        document.getElementById('arLog').textContent = '';
      } else {
        sawInProgress = true;   // 研究進行中 → 之後若轉完成才 reload
        setTimeout(pollAR, 4000);
      }
    }).catch(function () { setTimeout(pollAR, 6000); });
  }

  pollAR();
})();
