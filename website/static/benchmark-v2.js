(() => {
  const table = document.getElementById('v2-table');
  if (!table) return;
  for (const button of table.querySelectorAll('[data-sort]')) {
    button.addEventListener('click', () => {
      const th = button.closest('th');
      const key = button.dataset.sort;
      const ascending = th.getAttribute('aria-sort') === 'descending' ||
        (!th.hasAttribute('aria-sort') && ['p50', 'p95'].includes(key));
      for (const header of table.querySelectorAll('th[aria-sort]')) header.removeAttribute('aria-sort');
      th.setAttribute('aria-sort', ascending ? 'ascending' : 'descending');
      const rows = [...table.tBodies[0].rows];
      rows.sort((a, b) => (Number(a.dataset[key]) - Number(b.dataset[key])) * (ascending ? 1 : -1));
      table.tBodies[0].append(...rows);
      document.getElementById('sort-status').textContent = `已按${button.textContent.replace('↕', '').trim()}${ascending ? '升序' : '降序'}排列`;
    });
  }
  for (const link of table.querySelectorAll('tbody a')) link.addEventListener('click', () => {
    document.querySelector('.v2-details').open = true;
  });
})();
