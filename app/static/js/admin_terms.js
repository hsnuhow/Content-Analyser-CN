// 字詞過濾後台：候選建議分析。
// 自 admin_terms.html 的 inline <script> 抽出（CSP 強化）。
// Jinja 產生的 URL 透過按鈕的 data-url 屬性傳入（靜態 js 不經模板渲染）。
//
// 候選每列可三選一：略過 / 垃圾（→ term_filters）/ 必留 / 品牌（→ keep_terms）。
// 送出時依各列選擇，動態組出 pick[]（垃圾，含範圍）、keep[]、brand[] 三組 hidden 欄位。
(function () {
  var lastCands = [];   // 最近一次候選（供送出時依索引取回 term/scope/type）

  function esc(s) {
    return String(s || '').replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
    });
  }

  function renderCandidates(d) {
    var cands = d.candidates || [];
    lastCands = cands;
    var prot = d.n_protected_entities ? ('，白名單保護 ' + d.n_protected_entities + ' 個實體') : '';
    if (!cands.length) {
      document.getElementById('suggestLog').textContent = '沒有找到新候選（可能都已在清單/保留清單中）。';
      document.getElementById('suggestForm').classList.add('d-none');
      return;
    }
    document.getElementById('suggestLog').textContent =
      '找到 ' + cands.length + ' 個候選（' + (d.scope_label || ('共 ' + (d.n_docs || 0) + ' 篇')) + prot + '）。逐列選擇處理方式後套用。';
    var rows = cands.map(function (c, i) {
      var sc = (c.scope || []).join(',');
      // 預設：高重複/填充 → 垃圾；其餘（需複查）→ 略過（避免誤殺領域詞）
      var junkDefault = (c.kind === 'chrome' || c.kind === '填充');
      function radio(val, label, checked) {
        return '<label class="me-2 text-nowrap"><input type="radio" name="ch_' + i + '" value="' + val + '"' + (checked ? ' checked' : '') + '> ' + label + '</label>';
      }
      var choices = radio('skip', '略', !junkDefault) + radio('junk', '垃圾', junkDefault)
        + radio('keep', '留', false) + radio('brand', '品牌', false);
      return '<tr><td class="small">' + choices + '</td>'
        + '<td><code>' + esc(c.term) + '</code></td><td>' + esc(sc) + '</td>'
        + '<td><span class="badge bg-' + (c.kind === 'chrome' ? 'warning text-dark' : (c.kind === '填充' ? 'secondary' : 'light text-dark')) + '">' + esc(c.kind || '') + '</span></td>'
        + '<td>' + (c.disc || 0).toFixed(2) + '</td><td>' + (c.rep || 0).toFixed(1) + '×</td></tr>';
    }).join('');
    document.getElementById('suggestRows').innerHTML = rows;
    document.getElementById('suggestForm').classList.remove('d-none');
  }

  // 送出前：依各列 radio 選擇，組出 pick/keep/brand 隱藏欄位（垃圾帶範圍+media）
  function onSubmit(e) {
    var form = e.target;
    form.querySelectorAll('input.dynpick').forEach(function (n) { n.remove(); });
    function add(name, value) {
      var h = document.createElement('input');
      h.type = 'hidden'; h.name = name; h.value = value; h.className = 'dynpick';
      form.appendChild(h);
    }
    lastCands.forEach(function (c, i) {
      var sel = form.querySelector('input[name="ch_' + i + '"]:checked');
      var v = sel ? sel.value : 'skip';
      if (v === 'junk') {
        var sc = (c.scope || []).join(',');
        add('pick', c.term + ' | ' + sc + (c.type === 'media' ? ' | media' : ''));
      } else if (v === 'keep') {
        add('keep', c.term);
      } else if (v === 'brand') {
        add('brand', c.term);
      }
    });
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
    var form = document.getElementById('suggestForm');
    if (form) form.addEventListener('submit', onSubmit);
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
