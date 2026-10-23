(() => {
  function bindTabs(selector, select) {
    const tabs = [...document.querySelectorAll(selector)];
    tabs.forEach((button, index) => {
      button.addEventListener('click', () => select(button));
      button.addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 :
          (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
        select(tabs[next]);
        tabs[next].focus();
      });
    });
  }
  function activate(button, selector) {
    for (const tab of document.querySelectorAll(selector)) {
      const active = tab === button;
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
      document.getElementById(tab.getAttribute('aria-controls')).hidden = !active;
    }
  }
  bindTabs('[data-current-view]', button => activate(button, '[data-current-view]'));
  const table = document.getElementById('model-comparison');
  if (!table) return;
  for (const button of table.querySelectorAll('[data-model-sort]')) {
    button.addEventListener('click', () => {
      const header = button.closest('th');
      const key = button.dataset.modelSort;
      const ascending = header.getAttribute('aria-sort') === 'descending' ||
        (!header.hasAttribute('aria-sort') && ['name', 'p50', 'p95', 'cost'].includes(key));
      for (const th of table.querySelectorAll('th[aria-sort]')) th.removeAttribute('aria-sort');
      header.setAttribute('aria-sort', ascending ? 'ascending' : 'descending');
      const rows = [...table.tBodies[0].rows];
      rows.sort((a, b) => (key === 'name' ? a.dataset.name.localeCompare(b.dataset.name) :
        Number(a.dataset[key]) - Number(b.dataset[key])) * (ascending ? 1 : -1));
      table.tBodies[0].append(...rows);
      document.getElementById('model-sort-status').textContent = `已按${button.textContent.replace('↕', '').trim()}${ascending ? '升序' : '降序'}排列`;
    });
  }
})();
