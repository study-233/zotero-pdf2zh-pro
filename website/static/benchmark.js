(() => {
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
