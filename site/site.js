/* Shared site enhancements. Documentation remains readable without JavaScript. */
(function () {
  'use strict';
  function searchEntries(entries, query) {
    const terms = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    if (!terms.length) return [];
    return entries.map(entry => {
      const title = entry.title.toLocaleLowerCase(), section = entry.section.toLocaleLowerCase();
      const body = entry.text.toLocaleLowerCase();
      const matches = terms.every(term => (title + ' ' + section + ' ' + body).includes(term));
      const score = terms.reduce((sum, term) => sum + (title.includes(term) ? 6 : 0) + (section.includes(term) ? 4 : 0) + (body.includes(term) ? 1 : 0), 0);
      return {entry, score: matches ? score : 0};
    }).filter(result => result.score).sort((a, b) => b.score - a.score).slice(0, 16).map(result => result.entry);
  }
  function legacyTarget(base, hash) {
    return ['#results', '#method', '#compare'].includes(hash) ? base + 'benchmark/' + hash : null;
  }
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {searchEntries, legacyTarget};
    return;
  }
  const base = document.body.dataset.base;
  if (document.body.classList.contains('home-page')) {
    const redirect = () => {
      const target = legacyTarget(base, location.hash);
      if (target) location.replace(target);
    };
    redirect();
    window.addEventListener('hashchange', redirect);
  }
  const theme = document.getElementById('site-theme');
  const themeLabel = () => {
    const label = document.documentElement.dataset.theme === 'light' ? '切换到深色主题' : '切换到浅色主题';
    theme.setAttribute('aria-label', label);theme.title = label;
  };
  theme.hidden = false;themeLabel();
  theme.addEventListener('click', () => {
    BenchUI.applyTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light');themeLabel();
  });
  const search = document.getElementById('site-search');
  const searchButton = document.getElementById('site-search-open');
  const input = document.getElementById('search-input');
  const results = document.getElementById('search-results');
  const status = document.getElementById('search-status');
  let indexPromise, searchTicket = 0;
  const index = () => indexPromise ||= fetch(base + 'search-index.json').then(response => {
    if (!response.ok) throw Error('Search index unavailable');return response.json();
  }).catch(error => {indexPromise = null;throw error;});
  searchButton.hidden = false;
  function openSearch() {if (!search.open) search.showModal();input.focus();}
  searchButton.addEventListener('click', openSearch);
  document.addEventListener('keydown', event => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {event.preventDefault();openSearch();}
  });
  input.addEventListener('input', async () => {
    const ticket = ++searchTicket, query = input.value.trim();results.replaceChildren();
    if (!query) {status.textContent = '输入关键词，查找安装与使用方法。';return;}
    status.textContent = '正在搜索…';
    try {
      const entries = await index();if (ticket !== searchTicket) return;
      const matches = searchEntries(entries, query);
      status.textContent = matches.length ? '找到 ' + matches.length + ' 个相关章节' : '没有找到相关章节。试试“安装”“API”或“补译”。';
      for (const entry of matches) {
        const item = document.createElement('li'), link = document.createElement('a');link.href = entry.url;
        const title = document.createElement('strong');title.textContent = entry.title + (entry.section === entry.title ? '' : ' · ' + entry.section);
        const snippet = document.createElement('span');
        const firstTerm = query.toLocaleLowerCase().split(/\s+/)[0];
        const start = Math.max(0, entry.text.toLocaleLowerCase().indexOf(firstTerm) - 30);
        snippet.textContent = (start ? '…' : '') + entry.text.slice(start, start + 135) + (entry.text.length > start + 135 ? '…' : '');
        link.append(title, snippet);link.addEventListener('click', () => search.close());item.append(link);results.append(item);
      }
    } catch {if (ticket === searchTicket) status.textContent = '搜索暂时不可用，请使用教程目录；重新输入可重试。';}
  });
  input.addEventListener('keydown', event => {if (event.key === 'ArrowDown') {results.querySelector('a')?.focus();event.preventDefault();}});
  for (const dialog of document.querySelectorAll('#site-search, #image-dialog')) {
    dialog.querySelector('[data-close-dialog]').addEventListener('click', () => dialog.close());
    dialog.addEventListener('click', event => {if (event.target === dialog) {
      const rect = dialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
    }});
  }
  const imageDialog = document.getElementById('image-dialog');
  for (const img of document.querySelectorAll('.prose img, .zoom-image img')) {
    if (img.getAttribute('fetchpriority') !== 'high') img.loading = 'lazy';
    img.decoding = 'async';
    let button = img.closest('.zoom-image');
    if (!button) {button = document.createElement('button');button.type = 'button';button.className = 'zoom-image';img.replaceWith(button);button.append(img);}
    button.setAttribute('aria-label', '放大截图：' + img.alt);
    button.addEventListener('click', event => {event.preventDefault();imageDialog.querySelector('img').src = img.src;
      imageDialog.querySelector('img').alt = img.alt;imageDialog.querySelector('p').textContent = img.alt;imageDialog.showModal();});
  }
  for (const pre of document.querySelectorAll('.prose pre')) {
    const button = document.createElement('button');button.type = 'button';button.className = 'copy-code';button.textContent = '复制';button.setAttribute('aria-label', '复制代码');
    const message = document.createElement('span');message.className = 'sr-only';message.setAttribute('role', 'status');
    pre.append(button, message);
    button.addEventListener('click', async () => {
      try {await navigator.clipboard.writeText(pre.querySelector('code').textContent);button.textContent = '已复制';message.textContent = '代码已复制';}
      catch {button.textContent = '请手动复制';message.textContent = '无法访问剪贴板，已选中代码';const selection = window.getSelection(), range = document.createRange();range.selectNodeContents(pre.querySelector('code'));selection.removeAllRanges();selection.addRange(range);}
      setTimeout(() => {button.textContent = '复制';}, 2500);
    });
  }
  const directory = document.getElementById('guide-directory');
  if (directory) {
    const mobile = matchMedia('(max-width: 900px)');const update = () => {directory.open = !mobile.matches;};update();mobile.addEventListener('change', update);
  }
  const headings = [...document.querySelectorAll('.prose h2[id], .prose h3[id]')];
  if (headings.length && 'IntersectionObserver' in window) {
    const observer = new IntersectionObserver(entries => {
      const visible = entries.filter(entry => entry.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
      if (!visible) return;
      for (const link of document.querySelectorAll('.doc-toc nav a')) {
        if (decodeURIComponent(link.hash.slice(1)) === visible.target.id) link.setAttribute('aria-current', 'location');else link.removeAttribute('aria-current');
      }
    }, {rootMargin: '-100px 0px -60% 0px'});
    headings.forEach(heading => observer.observe(heading));
  }
})();
