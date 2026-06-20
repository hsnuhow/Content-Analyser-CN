// 登入/著陸頁：中英語言切換。
// 自 login.html 的 inline <script> 抽出（CSP 強化：移除 inline script 與 onclick 依賴）。
(function () {
  function setLang(l) {
    var root = document.getElementById('landing');
    if (!root) return;
    root.setAttribute('data-lang', l);
    document.querySelectorAll('.lang-btn').forEach(function (b) {
      b.classList.toggle('active', b.getAttribute('data-lang') === l);
    });
    try { localStorage.setItem('insightout_lang', l); } catch (e) {}
  }

  function init() {
    // 以 data-lang 綁定點擊（取代原本的 inline onclick="setLang(...)"）
    document.querySelectorAll('.lang-btn').forEach(function (b) {
      b.addEventListener('click', function () { setLang(b.getAttribute('data-lang')); });
    });
    // 記住上次選擇（預設中文）
    var saved = 'zh';
    try { saved = localStorage.getItem('insightout_lang') || 'zh'; } catch (e) {}
    setLang(saved === 'en' ? 'en' : 'zh');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
