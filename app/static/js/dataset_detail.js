// InsightOut — dataset_detail 頁面腳本
//
// CSP 強化：取代原模板內的 3 段 inline <script>。
// 各段以「元素是否存在 + data 屬性 URL 是否帶入」自我守衛，
// URL 由模板透過 data 屬性提供（dataset_status / research_status / extract_images_status）。
// 渲染邏輯與原 inline 版本完全一致。

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
    if (!logEl || !url) return;
    (function poll() {
      fetch(url)
        .then(function (r) { return r.json(); })
        .then(function (d) {
          document.getElementById('bar').style.width = (d.progress || 0) + '%';
          document.getElementById('pct').textContent = (d.progress || 0) + '%';
          document.getElementById('log').textContent = d.log || '';
          if (d.status === 'completed' || d.status === 'failed') { location.reload(); }
          else { setTimeout(poll, 3000); }
        }).catch(function () { setTimeout(poll, 5000); });
    })();
  })();

  // 2) 失敗項選擇器研究輪詢：#researchResult[data-research-url]
  (function () {
    var resultEl = document.getElementById('researchResult');
    var url = resultEl && resultEl.getAttribute('data-research-url');
    if (!resultEl || !url) return;
    (function pollR() {
      fetch(url)
        .then(function (r) { return r.json(); })
        .then(function (d) {
          document.getElementById('researchLog').textContent = d.log || d.status || '';
          if (d.status === 'completed') {
            var res = d.result || {}; var h = '';
            (res.candidates || []).forEach(function (c) { h += `<div class="alert alert-success py-2 mb-2"><b>${esc(c.domain)}</b> → 候選 <code>${esc(c.selector)}</code>（${c.validated_chars || 0} 字）— 待管理員於後台確認升級</div>`; });
            (res.diagnoses || []).forEach(function (g) { h += `<div class="alert alert-warning py-2 mb-2"><b>${esc(g.domain)}</b>：${esc(g.diagnosis)}</div>`; });
            document.getElementById('researchResult').innerHTML = h || '<div class="text-muted">無候選/診斷。</div>';
          } else if (d.status === 'failed') {
            document.getElementById('researchLog').textContent = '研究失敗：' + (d.log || '');
          } else { setTimeout(pollR, 4000); }
        }).catch(function () { setTimeout(pollR, 6000); });
    })();
  })();

  // 3) 主文大圖擷取輪詢：#imgResult[data-img-url]
  (function () {
    var imgEl = document.getElementById('imgResult');
    var url = imgEl && imgEl.getAttribute('data-img-url');
    if (!imgEl || !url) return;
    (function pollImg() {
      fetch(url)
        .then(function (r) { return r.json(); })
        .then(function (d) {
          document.getElementById('imgLog').textContent = d.log || d.status || '';
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
            document.getElementById('imgResult').innerHTML = h || '<div class="text-muted">無擷取到大圖。</div>';
          } else if (d.status === 'failed') {
            document.getElementById('imgLog').textContent = '擷取失敗：' + (d.log || '');
          } else if (d.status === 'none') {
            document.getElementById('imgLog').textContent = '（無擷取任務）';
          } else { setTimeout(pollImg, 4000); }
        }).catch(function () { setTimeout(pollImg, 6000); });
    })();
  })();
});
