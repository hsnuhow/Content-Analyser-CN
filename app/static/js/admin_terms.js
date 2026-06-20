// 字詞過濾後台：候選建議分析。
// 自 admin_terms.html 的 inline <script> 抽出（CSP 強化）。
// Jinja 產生的 URL 透過按鈕的 data-url 屬性傳入（靜態 js 不經模板渲染）。
(function () {
  function esc(s) {
    return (s || '').replace(/[&<>"]/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c];
    });
  }

  function renderCandidates(d) {
    var cands = d.candidates || [];
    var prot = d.n_protected_entities ? ('，白名單保護 ' + d.n_protected_entities + ' 個實體') : '';
    if (!cands.length) {
      document.getElementById('suggestLog').textContent = '沒有找到新候選（可能都已在清單中）。';
      return;
    }
    document.getElementById('suggestLog').textContent =
      '找到 ' + cands.length + ' 個候選（' + (d.scope_label || ('共 ' + (d.n_docs || 0) + ' 篇')) + prot + '）。勾選後加入。';
    var rows = cands.map(function (c) {
      var sc = (c.scope || []).join(',');
      var val = esc(c.term) + ' | ' + esc(sc) + (c.type === 'media' ? ' | media' : '');
      return '<tr><td><input class="form-check-input" type="checkbox" name="pick" value="' + esc(val) + '" ' + (c.kind === 'chrome' || c.kind === '填充' ? 'checked' : '') + '></td>'
        + '<td><code>' + esc(c.term) + '</code></td><td>' + esc(sc) + '</td>'
        + '<td><span class="badge bg-' + (c.kind === 'chrome' ? 'warning text-dark' : (c.kind === '填充' ? 'secondary' : 'light text-dark')) + '">' + esc(c.kind || '') + '</span></td>'
        + '<td>' + (c.disc || 0).toFixed(2) + '</td><td>' + (c.rep || 0).toFixed(1) + '×</td></tr>';
    }).join('');
    document.getElementById('suggestRows').innerHTML = rows;
    document.getElementById('suggestForm').classList.remove('d-none');
  }

  function fetchSuggest(url, btn) {
    btn.disabled = true;
    document.getElementById('suggestForm').classList.add('d-none');
    document.getElementById('suggestLog').textContent = '分析中…（可能需數十秒）';
    fetch(url).then(function (r) { return r.json(); }).then(function (d) {
      btn.disabled = false;
      if (d.error) { document.getElementById('suggestLog').textContent = '錯誤：' + d.error; return; }
      renderCandidates(d);
    }).catch(function (e) { btn.disabled = false; document.getElementById('suggestLog').textContent = '請求失敗：' + e; });
  }

  function init() {
    var allBtn = document.getElementById('suggestAllBtn');
    var oneBtn = document.getElementById('suggestBtn');
    if (allBtn) {
      allBtn.addEventListener('click', function () { fetchSuggest(allBtn.dataset.url, allBtn); });
    }
    if (oneBtn) {
      oneBtn.addEventListener('click', function () {
        var pid = (document.getElementById('suggestProj').value || '').trim();
        if (!pid) { document.getElementById('suggestLog').textContent = '請先填專案 ID（或用上方全庫學習）。'; return; }
        fetchSuggest(oneBtn.dataset.url + '?pid=' + encodeURIComponent(pid), oneBtn);
      });
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
