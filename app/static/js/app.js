// InsightOut — 全域前端腳本
//
// 註：舊的爬蟲任務輪詢邏輯（task-form）已於 Phase 3 移除。
// 分析任務的進度輪詢已改由各分析報告頁面（analysis_detail.html）自行處理。
//
// CSP 強化：以下通用 helper 取代各模板的 inline onclick/onsubmit，
// 讓 script-src 最終能移除 'unsafe-inline'。只對帶對應 data-* 屬性的元素生效，
// 不影響其他頁面。Jinja 變數放在 HTML 屬性值內（由模板渲染），故仍可帶動態文字。

// ─────────────────────────────────────────────────────────────────────────
// 共用輪詢 helper：window.poll(url, opts)
//
// 把原本散在各頁面、各自手刻的「定時 fetch 進度直到完成」邏輯收斂成一份。
// 重點是「嘗試上限」——為什麼重要：若後端任務卡在非終態（例如行程 crash 留下
// status=crawling、或 session 殘留指向一個已完成的 job），沒有上限的輪詢會每隔
// 幾秒永遠打一次伺服器。過去的「選擇器候選頁無限重整 / 失控輪詢」就是這樣來的。
// 把上限與終態處理集中在這裡，之後新增輪詢頁面就不會再各自寫錯。
//
// 用法：
//   poll(url, {
//     interval,        // 正常輪詢間隔毫秒（預設 3000）
//     errorInterval,   // 單次請求失敗後的重試間隔毫秒（預設 5000，退避用）
//     maxAttempts,     // 嘗試上限（預設 200；成功與失敗都計入，避免永遠重試）
//     onData(data, ctx) // 每次取得 JSON 後呼叫，回傳決定下一步：
//                       //   'continue' 繼續輪詢 / 'stop' 結束 / 'reload' 重新載入頁面
//     onTimeout()      // 達上限時呼叫（通常用來顯示「逾時請重整」）
//     onError(err)     // 單次請求失敗時呼叫（仍會自動重試直到上限）
//   })
// ctx 是跨輪共用的物件，可在 onData 內存放旗標（例如 sawInProgress）。
// ─────────────────────────────────────────────────────────────────────────
window.poll = function (url, opts) {
  opts = opts || {};
  var interval = opts.interval || 3000;
  var errorInterval = opts.errorInterval || 5000;
  var maxAttempts = opts.maxAttempts || 200;
  var ctx = {};
  (function tick(n) {
    if (n >= maxAttempts) { if (opts.onTimeout) opts.onTimeout(); return; }
    fetch(url).then(function (r) { return r.json(); }).then(function (data) {
      var action = opts.onData ? opts.onData(data, ctx) : 'stop';
      if (action === 'reload') { location.reload(); }
      else if (action === 'continue') { setTimeout(function () { tick(n + 1); }, interval); }
      // 'stop'（或任何其他回傳）→ 不再排程，輪詢結束
    }).catch(function (err) {
      if (opts.onError) opts.onError(err);
      setTimeout(function () { tick(n + 1); }, errorInterval);  // 失敗也計入上限
    });
  })(0);
};

// ─────────────────────────────────────────────────────────────────────────
// 共用 Markdown 渲染：window.renderMarkdown(targetEl, dataEl)
//
// 把資料島（<script type="application/json">，JSON 字串或純文字）渲染進目標元素。
// 守衛 marked / DOMPurify 是否載入：CDN 失敗時不丟例外，改以純文字安全降級
// （絕不在未消毒的情況下塞 HTML）。
// ─────────────────────────────────────────────────────────────────────────
window.renderMarkdown = function (targetEl, dataEl) {
  if (!targetEl || !dataEl) return;
  var md = '';
  try { md = JSON.parse(dataEl.textContent); } catch (e) { md = dataEl.textContent || ''; }
  if (window.marked && window.DOMPurify) {
    targetEl.innerHTML = DOMPurify.sanitize(marked.parse(md));
  } else {
    targetEl.textContent = md;  // 降級：純文字，避免 CDN 失敗時整頁空白或執行未消毒 HTML
  }
};

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
  //    只有「確實寫入成功」才顯示「已複製」；失敗或瀏覽器不支援（非 https 時 clipboard
  //    不可用）則顯示提示——避免明明沒複製到卻回報成功而誤導使用者。
  document.querySelectorAll('[data-copy-target]').forEach(function (el) {
    el.addEventListener('click', function () {
      var t = document.getElementById(el.getAttribute('data-copy-target'));
      if (!t) return;
      var val = (t.value !== undefined ? t.value : t.textContent) || '';
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(val)
          .then(function () { el.textContent = '已複製'; })
          .catch(function () { el.textContent = '複製失敗，請手動選取'; });
      } else {
        el.textContent = '無法複製（請手動選取）';
      }
    });
  });
});
