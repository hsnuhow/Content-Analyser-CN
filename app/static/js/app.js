// InsightOut — 全域前端腳本
//
// 註：舊的爬蟲任務輪詢邏輯（task-form）已於 Phase 3 移除。
// 分析任務的進度輪詢已改由各分析報告頁面（analysis_detail.html）自行處理。
//
// CSP 強化：以下通用 helper 取代各模板的 inline onclick/onsubmit，
// 讓 script-src 最終能移除 'unsafe-inline'。只對帶對應 data-* 屬性的元素生效，
// 不影響其他頁面。Jinja 變數放在 HTML 屬性值內（由模板渲染），故仍可帶動態文字。

document.addEventListener('DOMContentLoaded', function () {
  // 1) 表單送出前確認：<form data-confirm="訊息">（取代 onsubmit="return confirm(...)"）
  document.querySelectorAll('form[data-confirm]').forEach(function (f) {
    f.addEventListener('submit', function (e) {
      if (!window.confirm(f.getAttribute('data-confirm'))) e.preventDefault();
    });
  });

  // 2) 點擊前確認：<el data-confirm-click="訊息">（取代 onclick="return confirm(...)"）
  //    確認失敗即取消預設動作（含 submit 按鈕的送出）。
  document.querySelectorAll('[data-confirm-click]').forEach(function (el) {
    el.addEventListener('click', function (e) {
      if (!window.confirm(el.getAttribute('data-confirm-click'))) e.preventDefault();
    });
  });

  // 3) 點擊送出所在表單：<el data-submit-form>（取代 onclick="this.form.submit()"）
  document.querySelectorAll('[data-submit-form]').forEach(function (el) {
    el.addEventListener('click', function () {
      var form = el.closest('form');
      if (form) form.submit();
    });
  });

  // 4) 複製目標元素的值到剪貼簿並回饋：<el data-copy-target="目標元素id">
  //    （取代 onclick="navigator.clipboard.writeText(...); this.textContent='已複製'"）
  document.querySelectorAll('[data-copy-target]').forEach(function (el) {
    el.addEventListener('click', function () {
      var t = document.getElementById(el.getAttribute('data-copy-target'));
      if (!t) return;
      var val = (t.value !== undefined ? t.value : t.textContent) || '';
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(val);
      }
      el.textContent = '已複製';
    });
  });
});
