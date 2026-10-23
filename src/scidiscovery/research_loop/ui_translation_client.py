"""Browser cache reader. Provider work starts only in the explicit click handler."""
import base64
import hashlib

SCRIPT = r'''
(() => {
  const bar = document.querySelector('[data-translation-url]');
  if (!bar) return;
  const base = bar.dataset.translationUrl, status = bar.querySelector('[data-translation-status]');
  const start = bar.querySelector('[data-translation-start]'), toggle = bar.querySelector('[data-translation-toggle]');
  const entries = new Map(), seen = new Set();
  let state, original = false, busy = false, timer, scheduled, active = false, sessionExpired = false, message = "";
  const nodes = () => [...document.querySelectorAll('[data-translation-source]')].filter(node => node.getClientRects().length);
  const sourceOf = node => JSON.parse(node.dataset.translationSource);
  const sources = () => [...new Set(nodes().map(sourceOf))];
  const batches = texts => {
    const result = []; let batch = [], bytes = 0;
    for (const text of texts) {
      const size = new TextEncoder().encode(text).length;
      if (batch.length && (batch.length >= 32 || bytes + size > 150000)) { result.push(batch); batch = []; bytes = 0; }
      batch.push(text); bytes += size;
    }
    if (batch.length) result.push(batch);
    return result;
  };
  async function readResponse(response) {
    let value;
    try { value = await response.json(); } catch (_) {
      throw new Error('工作台响应无法读取，请重新打开阅读与翻译页面核对设置。');
    }
    if (!response.ok) {
      const problem = value?.error;
      sessionExpired = response.status === 403;
      const detail = problem && typeof problem === 'object' ? [problem.title, problem.cause, problem.hint,
        problem.field ? '相关字段：' + problem.field : '', problem.reference ? '错误编号：' + problem.reference : ''].filter(Boolean).join(' · ') : '';
      throw new Error(detail || (sessionExpired ? '浏览会话已失效，请从终端本次 management_url 重新打开工作台。' : '翻译请求失败，请打开阅读与翻译检查设置。'));
    }
    sessionExpired = false;
    return value;
  }
  const failureReason = error => ({
    'Translation did not complete. Request a retry explicitly.':'翻译未完成，请检查阅读设置；确认后可点击重试。',
    'Translation response was invalid. Request a retry explicitly.':'翻译模型返回了无效内容；原文已保留，确认后可点击重试。',
    'Translation launcher is unavailable. Check application settings.':'翻译启动命令不可用，请在阅读与翻译中检查启动器设置。',
    'Translation timed out. Request a retry explicitly.':'翻译超时，请检查超时设置；确认后可点击重试。',
    'Translation output exceeded the application limit.':'翻译输出超过应用上限；请缩小本次翻译内容后重试。',
    'Translation attempted a tool action. Request a retry explicitly.':'翻译模型尝试调用工具，翻译已停止；原文已保留。'
  }[error] || (typeof error === 'string' && error ? error : '翻译未完成；原文已保留，请检查阅读设置。'));
  async function request(action, texts) {
    const response = await fetch(base + '/' + action, {method:'POST', credentials:'same-origin',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify({texts, nonce:state.nonce, csrf:state.csrf[action]})});
    const value = await readResponse(response);
    if (value.config_generation !== state.config_generation) { entries.clear(); seen.clear(); }
    state.config_generation = value.config_generation; state.target_language = value.target_language;
    state.configured = value.configured;
    return value;
  }
  function paint() {
    const texts = sources(), failures = new Set(); let ready = 0, pending = 0, failed = 0;
    for (const source of texts) {
      const entry = entries.get(source);
      if (entry?.status === 'cached') ready++;
      if (entry?.status === 'pending') pending++;
      if (entry?.status === 'failed') { failed++; failures.add(failureReason(entry.error)); }
    }
    for (const node of nodes()) {
      const source = sourceOf(node), entry = entries.get(source);
      const text = !original && entry?.status === 'cached' ? entry.translation : source;
      if (node.textContent !== text) node.textContent = text;
      node.dataset.translationStatus = entry?.status || 'missing';
      if (entry?.error) node.title = failureReason(entry.error);
      else node.removeAttribute('title');
    }
    status.textContent = (state.configured && state.enabled ? state.target_language : '尚未启用翻译') + ' · 已缓存 ' + ready + '/' + texts.length
      + (pending ? ' · 处理中 ' + pending : '') + (failed ? ' · 失败 ' + failed + ' · ' + [...failures].slice(0, 3).join('；')
        + (failures.size > 3 ? '；另有 ' + (failures.size - 3) + ' 类失败，悬停对应原文查看原因。' : '') : '') + (state.supported === false ? ' · ' + state.unsupported_reason : '') + (message ? ' · ' + message : '');
    start.disabled = state.supported === false || sessionExpired || !state.configured || !state.enabled || !state.csrf.start || busy || !texts.some(text => ['missing','failed'].includes(entries.get(text)?.status || 'missing'));
    toggle.textContent = original ? '显示译文' : '显示原文';
    clearTimeout(timer);
    if (pending && !busy && !sessionExpired) timer = setTimeout(() => lookup(texts.filter(text => entries.get(text)?.status === 'pending')), 1500);
  }
  async function lookup(texts) {
    if (busy || !texts.length) return;
    busy = true; paint();
    texts.forEach(text => seen.add(text));
    try {
      for (const batch of batches(texts)) {
        const value = await request('lookup', batch); message = '';
        for (const entry of value.entries) { entries.set(entry.source, entry); seen.add(entry.source); }
      }
    } catch (error) { message = error.message; }
    finally { busy = false; paint(); const fresh = sources().filter(text => !seen.has(text)); if (fresh.length && !sessionExpired) scheduled = setTimeout(() => lookup(fresh), 100); }
  }
  start.addEventListener('click', async () => {
    if (busy || !state) return;
    const missing = sources().filter(text => ['missing','failed'].includes(entries.get(text)?.status || 'missing'));
    busy = true; paint();
    try {
      for (const batch of batches(missing)) {
        const value = await request('start', batch); message = '';
        for (const entry of value.entries) { entries.set(entry.source, entry); seen.add(entry.source); }
      }
    } catch (error) { message = error.message; }
    finally { busy = false; paint(); }
  });
  toggle.addEventListener('click', () => { if (!state) return; original = !original; paint(); });
  function refresh() {
    if (!active) return;
    clearTimeout(scheduled);
    scheduled = setTimeout(() => { paint(); lookup(sources().filter(text => !seen.has(text))); }, 100);
  }
  document.addEventListener('toggle', refresh, true);
  document.addEventListener('change', refresh);
  const observer = new MutationObserver(records => {
    if (!active || !records.some(record => [...record.addedNodes].some(node => node.nodeType === 1))) return;
    refresh();
  });
  observer.observe(document.querySelector('main'), {childList:true, subtree:true});
  async function refreshState() {
    if (busy) { scheduled = setTimeout(refreshState, 150); return; }
    try {
      const response = await fetch(base + '/state', {credentials:'same-origin'});
      const next = await readResponse(response);
      if (state && next.config_generation !== state.config_generation) { entries.clear(); seen.clear(); }
      state = next; active = true; paint();
      await lookup(sources().filter(text => !seen.has(text)));
    } catch (error) { message = error.message; if (state) paint(); else status.textContent = message; }
  }
  window.addEventListener('focus', refreshState);
  refreshState();
})();
'''
CSP = "'sha256-" + base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode() + "'"

TOOLBAR = ('<aside class="translation-tools" data-translation-url="/translation" aria-label="阅读与翻译">'
    '<a href="/reading">阅读与翻译</a> '
    '<button type="button" class="secondary-button" data-translation-start disabled>翻译缺失内容</button> '
    '<button type="button" class="secondary-button" data-translation-toggle>显示原文</button> '
    '<span class="note" data-translation-status role="status">正在读取翻译缓存…</span></aside>')
