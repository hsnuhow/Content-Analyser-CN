// 延伸報告頁：把 Markdown 渲染進 #markdown-output。
// 自 derived_report.html 的 inline <script> 抽出（CSP 強化）。
// Markdown 內容由 <script type="application/json" id="derived-md"> 資料島帶入
// （type=application/json 非執行型，不受 script-src 管，移除 unsafe-inline 後仍可用）。
(function () {
  var el = document.getElementById('markdown-output');
  var dataEl = document.getElementById('derived-md');
  if (!el || !dataEl) return;
  var md = '';
  try { md = JSON.parse(dataEl.textContent); } catch (e) { md = dataEl.textContent || ''; }
  el.innerHTML = DOMPurify.sanitize(marked.parse(md));
})();
