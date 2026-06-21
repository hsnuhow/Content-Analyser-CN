// InsightOut — dataset_detail 頁面腳本
//
// CSP 強化：取代原模板內的 3 段 inline <script>。
// 各段以「元素是否存在 + data 屬性 URL 是否帶入」自我守衛，
// URL 由模板透過 data 屬性提供（dataset_status / research_status / extract_images_status）。
// 三個輪詢都走 app.js 的共用 window.poll（內建嘗試上限——若後端任務卡在非終態，
// 不會像舊版那樣每幾秒永遠打一次伺服器）。

document.addEventListener('DOMContentLoaded', function () {
  var esc = function (s) {
    return String(s || '').replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  };

  // 1) 爬取進度輪詢（crawling 時）：#log[data-status-url]
  (function () {
    var logEl = document.getElementById('log');
    var url = logEl && logEl.getAttribute('data-status-url');
    if (!url) return;
    poll(url, {
      interval: 3000, maxAttempts: 400,   // 約 20 分鐘上限（大批次爬取可能較久）
      onData: function (d) {
        var bar = document.getElementById('bar');
        var pct = document.getElementById('pct');
        if (bar) bar.style.width = (d.progress || 0) + '%';
        if (pct) pct.textContent = (d.progress || 0) + '%';
        logEl.textContent = d.log || '';
        return (d.status === 'completed' || d.status === 'failed') ? 'reload' : 'continue';
      },
      onTimeout: function () { logEl.textContent = (logEl.textContent || '') + '\n⚠️ 輪詢逾時，請重新整理頁面確認狀態。'; }
    });
  })();

  // 2) 失敗項選擇器研究輪詢：#researchResult[data-research-url]
  //    完成 → 渲染候選/診斷後停止（不重新載入）；卡住達上限 → 提示。
  (function () {
    var resultEl = document.getElementById('researchResult');
    var url = resultEl && resultEl.getAttribute('data-research-url');
    if (!url) return;
    var logEl = document.getElementById('researchLog');
    poll(url, {
      interval: 4000, errorInterval: 6000, maxAttempts: 150,   // 約 10 分鐘上限
      onData: function (d) {
        if (logEl) logEl.textContent = d.log || d.status || '';
        if (d.status === 'completed') {
          var res = d.result || {}; var h = '';
          (res.candidates || []).forEach(function (c) { h += `<div class="alert alert-success py-2 mb-2"><b>${esc(c.domain)}</b> → 候選 <code>${esc(c.selector)}</code>（${c.validated_chars || 0} 字）— 待管理員於後台確認升級</div>`; });
          (res.diagnoses || []).forEach(function (g) { h += `<div class="alert alert-warning py-2 mb-2"><b>${esc(g.domain)}</b>：${esc(g.diagnosis)}</div>`; });
          resultEl.innerHTML = h || '<div class="text-muted">無候選/診斷。</div>';
          return 'stop';
        }
        if (d.status === 'failed') { if (logEl) logEl.textContent = '研究失敗：' + (d.log || ''); return 'stop'; }
        return 'continue';
      },
      onTimeout: function () { if (logEl) logEl.textContent = '研究仍在進行，請稍後重新整理頁面查看。'; }
    });
  })();

  // 3) 主文大圖擷取輪詢：#imgResult[data-img-url]
  (function () {
    var imgEl = document.getElementById('imgResult');
    var url = imgEl && imgEl.getAttribute('data-img-url');
    if (!url) return;
    var logEl = document.getElementById('imgLog');
    poll(url, {
      interval: 4000, errorInterval: 6000, maxAttempts: 150,   // 約 10 分鐘上限
      onData: function (d) {
        if (logEl) logEl.textContent = d.log || d.status || '';
        if (d.status === 'completed') {
          var rs = d.results || []; var h = `<div class="mb-2 small text-muted">共 ${d.n_images || 0} 張大圖</div>`;
          rs.forEach(function (it) {
            if (!(it.images || []).length) return;
            h += `<div class="mb-3"><div class="small fw-semibold mb-1"><a href="${esc(it.url)}" target="_blank" rel="noopener" class="text-decoration-none">${esc(it.url)}</a> <span class="text-muted">（${it.count || 0} 張・${esc(it.source || '')}）</span></div><div class="d-flex flex-wrap gap-2">`;
            (it.images || []).forEach(function (im) {
              h += `<a href="${esc(im.src)}" target="_blank" rel="noopener" title="${esc(im.alt)}"><img src="${esc(im.src)}" style="height:90px;width:auto;border-radius:6px;border:1px solid #ddd;object-fit:cover" loading="lazy"></a>`;
            });
            h += `</div></div>`;
          });
          imgEl.innerHTML = h || '<div class="text-muted">無擷取到大圖。</div>';
          return 'stop';
        }
        if (d.status === 'failed') { if (logEl) logEl.textContent = '擷取失敗：' + (d.log || ''); return 'stop'; }
        if (d.status === 'none') { if (logEl) logEl.textContent = '（無擷取任務）'; return 'stop'; }
        return 'continue';
      },
      onTimeout: function () { if (logEl) logEl.textContent = '擷取仍在進行，請稍後重新整理頁面查看。'; }
    });
  })();
});
