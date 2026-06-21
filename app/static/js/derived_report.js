// 延伸報告頁：把 Markdown 渲染進 #markdown-output（走 app.js 的共用 renderMarkdown，
// 內含 marked/DOMPurify 載入守衛 + JSON 資料島解析）。
// 自 derived_report.html 的 inline <script> 抽出（CSP 強化）。Markdown 內容由
// <script type="application/json" id="derived-md"> 資料島帶入（非執行型，不受 script-src 管）。
// 在 DOMContentLoaded 後執行，確保 app.js 已先定義好 window.renderMarkdown。
(function () {
  function run() {
    renderMarkdown(document.getElementById('markdown-output'), document.getElementById('derived-md'));
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run);
  else run();
})();
