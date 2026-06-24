// project_detail.html 專屬前端腳本
//
// CSP 強化：把原本 inline 的 <script> 與 onclick/oninput handler 外部化。
// 每段邏輯各自守衛所需 DOM 元素存在（owner-only 卡片不存在時直接 return），
// 不影響沒有該元素的其他角色或頁面。
// CSRF token 不硬編於靜態 js，改從頁面既有的隱藏欄位 input[name="csrf_token"] 讀取。

document.addEventListener('DOMContentLoaded', function () {

  // ── 區塊 1：LLM 模型選擇器（僅 Owner 角色有此卡片）─────────────
  // 守衛：#providerSel 不存在（非 owner）即略過整段。
  (function () {
    if (!document.getElementById('providerSel')) return;

    const CURATED = {
      gemini: ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"],
      claude: ["claude-sonnet-4-5", "claude-opus-4-1", "claude-3-5-haiku-latest"],
      openai: ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "o4-mini", "o3"]
    };
    const CUSTOM = "__custom__";
    const providerSel = document.getElementById('providerSel');
    const modelSel = document.getElementById('modelSel');
    const modelCustom = document.getElementById('modelCustom');
    const modelHidden = document.getElementById('modelHidden');
    const modelMsg = document.getElementById('modelMsg');

    // API Key 取得網址（依供應商動態提示；網址經瀏覽器實測確認導向，2026-06）。
    const KEY_INFO = {
      gemini: { name: 'Gemini', url: 'https://aistudio.google.com/apikey', label: 'Google AI Studio' },
      claude: { name: 'Claude', url: 'https://platform.claude.com/settings/keys', label: 'Anthropic Console' },
      openai: { name: 'ChatGPT（OpenAI）', url: 'https://platform.openai.com/api-keys', label: 'OpenAI Platform' }
    };
    const apiKeyHint = document.getElementById('apiKeyHint');
    function updateHint() {
      if (!apiKeyHint) return;
      const info = KEY_INFO[providerSel.value];
      apiKeyHint.innerHTML = info
        ? '🔑 到 <a href="' + info.url + '" target="_blank" rel="noopener noreferrer">' + info.label + '</a> 取得 ' + info.name + ' API 金鑰（需自行登入並綁定信用卡付費）。'
        : '';
    }

    function toggleCustom(show) { modelCustom.classList.toggle('d-none', !show); }
    function syncHidden() { modelHidden.value = (modelSel.value === CUSTOM) ? modelCustom.value.trim() : modelSel.value; }
    function populate(list, selected) {
      modelSel.innerHTML = "";
      (list || []).forEach(function (m) {
        const o = document.createElement('option'); o.value = m; o.textContent = m; modelSel.appendChild(o);
      });
      const oc = document.createElement('option'); oc.value = CUSTOM; oc.textContent = "其他（自填）"; modelSel.appendChild(oc);
      if (selected && (list || []).indexOf(selected) >= 0) { modelSel.value = selected; toggleCustom(false); }
      else if (selected) { modelSel.value = CUSTOM; modelCustom.value = selected; toggleCustom(true); }
      else { modelSel.value = (list && list[0]) || CUSTOM; toggleCustom(modelSel.value === CUSTOM); }
      syncHidden();
    }
    modelSel.addEventListener('change', function () { toggleCustom(modelSel.value === CUSTOM); syncHidden(); });
    modelCustom.addEventListener('input', syncHidden);
    providerSel.addEventListener('change', function () { populate(CURATED[providerSel.value], ''); modelMsg.textContent = ''; updateHint(); });
    populate(CURATED[providerSel.value], modelHidden.value);
    updateHint();

    const refreshBtn = document.getElementById('refreshModels');
    if (refreshBtn) {
      refreshBtn.addEventListener('click', function () {
        modelMsg.textContent = '取得中…';
        const base = refreshBtn.getAttribute('data-models-url');
        fetch(base + "?provider=" + encodeURIComponent(providerSel.value))
          .then(r => r.json()).then(d => {
            if (d.models && d.models.length) { populate(d.models, modelHidden.value); modelMsg.textContent = '已更新為最新模型清單'; }
            else { modelMsg.textContent = d.error || '無法取得'; }
          }).catch(() => { modelMsg.textContent = '取得失敗'; });
      });
    }
  })();

  // ── 區塊 2：溫度 slider 即時顯示（僅 Owner 卡片）─────────────
  (function () {
    var tr = document.getElementById('tempRange');
    if (tr) {
      tr.addEventListener('input', function () {
        var v = document.getElementById('tempVal');
        if (v) v.textContent = tr.value;
      });
    }
  })();

  // ── 共用：從頁面既有隱藏欄位取 CSRF token ─────────────
  function getCsrf() {
    return (document.querySelector('input[name="csrf_token"]') || {}).value || '';
  }

  // ── 區塊 3a：品牌聲量探勘（#bsBtn，僅 Owner/Editor）─────────────
  (function () {
    var btn = document.getElementById('bsBtn');
    if (!btn) return;
    btn.addEventListener('click', function () {
      var topic = document.getElementById('bsTopic').value.trim();
      var brands = document.getElementById('bsBrands').value.trim();
      if (!topic || !brands) { document.getElementById('bsLog').textContent = '請填主題與至少一個品牌。'; return; }
      btn.disabled = true;
      document.getElementById('bsLog').textContent = '品牌聲量分析中…（每品牌一次搜尋情報，數十秒）';
      var fd = new FormData();
      fd.append('topic', topic); fd.append('brands', brands); fd.append('csrf_token', getCsrf());
      fetch(btn.getAttribute('data-url'), { method: 'POST', body: fd })
        .then(r => r.json()).then(d => {
          if (d.error) { btn.disabled = false; document.getElementById('bsLog').textContent = '錯誤：' + d.error; return; }
          document.getElementById('bsLog').textContent = '完成（' + (d.count || 0) + ' 個品牌），重新整理中…';
          location.reload();
        }).catch(e => { btn.disabled = false; document.getElementById('bsLog').textContent = '請求失敗：' + e; });
    });
  })();

  // ── 區塊 3b：關鍵字推薦清單（#discBtn，僅 Owner/Editor）─────────────
  (function () {
    var btn = document.getElementById('discBtn');
    if (!btn) return;
    btn.addEventListener('click', function () {
      var q = document.getElementById('discQ').value.trim();
      if (!q) { document.getElementById('discLog').textContent = '請先輸入關鍵字。'; return; }
      btn.disabled = true;
      document.getElementById('discLog').textContent = '搜尋情報分析中…（grounding + 解析，可能需數十秒，請稍候）';
      var fd = new FormData();
      fd.append('q', q); fd.append('csrf_token', getCsrf());
      fetch(btn.getAttribute('data-url'), { method: 'POST', body: fd })
        .then(r => r.json()).then(d => {
          if (d.error) { btn.disabled = false; document.getElementById('discLog').textContent = '錯誤：' + d.error; return; }
          document.getElementById('discLog').textContent = '已產生推薦清單（' + (d.count || 0) + ' 條），重新整理中…';
          location.reload();
        }).catch(e => { btn.disabled = false; document.getElementById('discLog').textContent = '請求失敗：' + e; });
    });
  })();

});
