(() => {
  'use strict';
  function speedFrontier(models) {
    return models.filter(a => !models.some(b => b.p50 <= a.p50 && b.quality >= a.quality &&
      (b.p50 < a.p50 || b.quality > a.quality))).sort((a, b) => a.p50 - b.p50);
  }
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {speedFrontier};
    return;
  }
  const $ = id => document.getElementById(id);
  const main = document.querySelector('.benchmark-explorer');
  if (!main) return;
  const table = $('model-comparison'), chart = $('benchmark-chart');
  const models = [...table.tBodies[0].rows].map(row => ({row, id: row.dataset.id, name: row.dataset.name,
    color: Number(row.dataset.color), quality: Number(row.dataset.quality), p50: Number(row.dataset.p50),
    p95: Number(row.dataset.p95), cost: Number(row.dataset.cost), successes: Number(row.dataset.successes),
    samples: Number(row.dataset.samples)}));
  const selected = new Set(models.map(m => m.id));
  const maxSpeed = Math.ceil(Math.max(...models.map(m => m.p50)) * 1.18 / 5) * 5;
  const maxCost = Math.ceil(Math.max(...models.map(m => m.cost)) * 1.18 / .05) * .05;
  const qualityRange = [90, 101];
  const defaultCamera = () => ({eye: main.clientWidth < 600 ? {x: 1.98, y: 2.16, z: 1.32} :
    {x: 1.65, y: 1.8, z: 1.1}, center: {x: 0, y: 0, z: -.1}});
  let mode = 'speed', frontier = true, focused = null, camera = defaultCamera(), revision = 0;
  let queue = Promise.resolve(), ready = false, resizeTimer;
  const color = m => getComputedStyle(main).getPropertyValue('--model-' + m.color).trim();
  const css = key => getComputedStyle(document.documentElement).getPropertyValue('--' + key).trim();
  const visible = () => models.filter(m => selected.has(m.id));
  const pointData = m => [m.id, m.quality, m.p50, m.p95, m.cost, m.successes, m.samples];
  const tooltip = '<b>%{fullData.name}</b><br>质量 %{customdata[1]:.2f} / 100' +
    '<br>P50 %{customdata[2]:.2f} s · P95 %{customdata[3]:.2f} s' +
    '<br>已记录金额 $%{customdata[4]:.5f}<br>成功 %{customdata[5]} / %{customdata[6]}<extra></extra>';

  function detail(model) {
    const target = $('chart-selection');
    target.replaceChildren();
    if (!model) {
      const hint = document.createElement('span');hint.className = 'selection-placeholder';
      hint.textContent = '悬停或点选模型，查看完整数据';target.append(hint);return;
    }
    const name = document.createElement('span');name.className = 'selection-name';
    name.style.setProperty('--model-color', color(model));
    const dot = document.createElement('span');dot.className = 'model-dot';dot.setAttribute('aria-hidden', 'true');
    name.append(dot, document.createTextNode(model.name));target.append(name);
    for (const [label, value] of [['质量', model.quality.toFixed(2)], ['P50', model.p50.toFixed(2) + ' s'],
      ['P95', model.p95.toFixed(2) + ' s'], ['成功', model.successes + ' / ' + model.samples],
      ['已记录金额', '$' + model.cost.toFixed(5)]]) {
      const item = document.createElement('span'), number = document.createElement('strong');
      number.textContent = value;item.append(document.createTextNode(label), number);target.append(item);
    }
  }
  function focusModel(id) {
    focused = id;
    for (const m of models) {
      const active = m.id === id;m.row.dataset.selected = String(active);
      m.row.querySelector('[data-model-select]').setAttribute('aria-pressed', String(active));
    }
    detail(models.find(m => m.id === id));
    render();
  }
  function syncModels() {
    for (const m of models) m.row.hidden = !selected.has(m.id);
    document.querySelectorAll('[data-model-toggle]').forEach(button =>
      button.setAttribute('aria-pressed', String(selected.has(button.dataset.modelToggle))));
    $('visible-model-count').textContent = selected.size + ' / ' + models.length;
    $('table-empty').hidden = selected.size > 0;
    if (focused && !selected.has(focused)) {focused = null;detail(null);}
    for (const m of models) {
      m.row.dataset.selected = String(m.id === focused);
      m.row.querySelector('[data-model-select]').setAttribute('aria-pressed', String(m.id === focused));
    }
  }
  function annotations(items, width, height) {
    const maximum = mode === 'speed' ? maxSpeed : maxCost;
    const mobile = width < 600, size = mobile ? 11 : 13, boxes = [];
    const plotWidth = width - 76, plotHeight = height - 85;
    return items.map((m, index) => {
      const px = (1 - (mode === 'speed' ? m.p50 : m.cost) / maximum) * plotWidth;
      const py = (qualityRange[1] - m.quality) / (qualityRange[1] - qualityRange[0]) * plotHeight;
      if (mobile) return {x: mode === 'speed' ? m.p50 : m.cost, y: m.quality, text: m.name,
        ax: 4 - px, ay: (index + .6) / (items.length + .2) * plotHeight - py,
        xanchor: 'left', yanchor: 'middle', showarrow: true, arrowhead: 0,
        arrowcolor: color(m), arrowwidth: 1, bgcolor: css('bg'), bordercolor: css('line'),
        borderpad: 5, font: {size, color: css('text')}, captureevents: false};
      const w = m.name.length * size * .67 + 16, h = size + 20;
      const choices = [];
      for (const distance of [20, 48, 78, 108]) for (const side of [-1, 1]) for (const direction of [-1, 1]) {
        const dx = side * 16, dy = direction * distance;
        const box = {left: px + dx - (side < 0 ? w : 0), top: py + dy - h / 2, w, h};
        const overflow = Math.max(0, -box.left) + Math.max(0, box.left + w - plotWidth) +
          Math.max(0, -box.top) + Math.max(0, box.top + h - plotHeight);
        const collisions = boxes.filter(b => box.left < b.left + b.w + 6 && box.left + w + 6 > b.left &&
          box.top < b.top + b.h + 6 && box.top + h + 6 > b.top).length;
        choices.push({box, dx, dy, side, score: collisions * 10000 + overflow * 500 + distance});
      }
      choices.sort((a, b) => a.score - b.score);const best = choices[0];boxes.push(best.box);
      return {x: mode === 'speed' ? m.p50 : m.cost, y: m.quality, text: m.name,
        ax: best.dx, ay: best.dy, xanchor: best.side < 0 ? 'right' : 'left', yanchor: 'middle',
        showarrow: true, arrowhead: 0, arrowwidth: 1, arrowcolor: color(m),
        bgcolor: css('bg'), bordercolor: css('line'), borderwidth: 1, borderpad: 5,
        font: {size, color: css('text')}, captureevents: false};
    });
  }
  function baseLayout() {
    return {width: chart.clientWidth, height: chart.clientHeight, paper_bgcolor: css('bg'),
      plot_bgcolor: css('bg'), font: {family: '-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif', color: css('muted'), size: 13},
      showlegend: false, hoverlabel: {bgcolor: css('raised'), bordercolor: css('line'),
        font: {size: 13, color: css('text')}}, uirevision: mode + revision,
      margin: {l: 48, r: 28, t: 25, b: 60}};
  }
  async function drawChart() {
    const items = visible(), layout = baseLayout(), traces = [];
    const is3d = mode === 'value', small = chart.clientWidth < 600;
    $('frontier-toggle').hidden = mode !== 'speed';
    $('rotate-left').hidden = $('rotate-right').hidden = !is3d;
    $('chart-panel').setAttribute('aria-labelledby', 'tab-' + mode);
    $('chart-metric').textContent = is3d ? '已记录金额 × P50 耗时 × 翻译质量' : '翻译质量 / 100';
    $('chart-gesture').textContent = is3d ? '拖动旋转 · 双指缩放' : '拖动平移 · 滚轮缩放';
    chart.setAttribute('aria-label', is3d ? '价格、速度与质量三维图，方向键旋转，加减键缩放' :
      '质量与' + (mode === 'speed' ? '速度' : '费用') + '交互图，加减键缩放');
    if (is3d) {
      layout.margin = {l: small ? 20 : 0, r: small ? 20 : 0, t: 15, b: small ? 20 : 0};
      const axis = (title, range, tickformat) => ({title: {text: title, font: {size: small ? 10 : 12}},
        range, tickformat, tickfont: {size: small ? 9 : 11}, gridcolor: css('line'), zerolinecolor: css('line'),
        showbackground: true, backgroundcolor: css('bg'), color: css('muted'), showspikes: false, nticks: 5});
      layout.scene = {camera, dragmode: 'orbit', aspectmode: 'manual', aspectratio: {x: 1.25, y: 1.25, z: .95},
        xaxis: axis('已记录金额 / USD', [0, maxCost], '$.2f'),
        yaxis: axis('P50 耗时 / s', [0, maxSpeed], '.0f'),
        zaxis: axis('翻译质量 / 100', [90, 101], '.0f'), annotations: []};
      const offsets = [[-24, -64], [24, -30], [-24, 18], small ? [16, 28] : [-20, -25],
        [24, -10], small ? [-16, 20] : [20, 20], [-20, 25]];
      for (const m of items) {
        traces.push({type: 'scatter3d', mode: 'lines', x: [m.cost, m.cost], y: [m.p50, m.p50],
          z: [90, m.quality], line: {color: color(m), width: 2, dash: 'dot'}, opacity: .28, hoverinfo: 'skip', showlegend: false});
        traces.push({type: 'scatter3d', mode: 'markers', name: m.name, x: [m.cost], y: [m.p50], z: [m.quality],
          ids: [m.id],
          marker: {size: focused === m.id ? 9 : 6, color: color(m), opacity: !focused || focused === m.id ? 1 : .5,
            line: {color: css('bg'), width: 1}}, customdata: [pointData(m)], hovertemplate: tooltip});
        const [ax, ay] = offsets[m.color];
        layout.scene.annotations.push({x: m.cost, y: m.p50, z: m.quality, text: m.name,
          ax, ay, xanchor: ax < 0 ? 'right' : 'left', yanchor: 'middle', showarrow: true, arrowhead: 0,
          arrowcolor: color(m), arrowwidth: 1, bgcolor: css('bg'), bordercolor: css('line'), borderpad: 4,
          font: {size: small ? 10 : 12, color: css('text')}});
      }
      if (items.length) {
        traces.push({type: 'scatter3d', mode: 'markers', x: [0], y: [0], z: [100], name: '理想方向',
        marker: {size: 5, symbol: 'diamond-open', color: css('accent')},
        hovertemplate: '金额更低 · 耗时更短 · 质量更高<extra></extra>'});
        layout.scene.annotations.push({x: 0, y: 0, z: 100, text: '理想方向', showarrow: true,
          ax: 25, ay: -70, xanchor: 'left', arrowhead: 2, arrowcolor: css('accent'),
          font: {size: small ? 10 : 12, color: css('accent')}});
      }
    } else {
      layout.dragmode = 'pan';
      layout.xaxis = {title: {text: mode === 'speed' ? 'P50 耗时 / s　·　更快 →' : '已记录金额 / USD　·　更便宜 →',
        font: {size: small ? 11 : 13}, standoff: 20}, range: [mode === 'speed' ? maxSpeed : maxCost, 0],
        gridcolor: css('line'), zeroline: false, tickformat: mode === 'cost' ? '$.2f' : '.0f', nticks: small ? 5 : 8,
        tickfont: {size: small ? 11 : 13}, fixedrange: false};
      layout.yaxis = {range: qualityRange, gridcolor: css('line'), zeroline: false, dtick: 2,
        tickfont: {size: small ? 11 : 13}};
      layout.annotations = annotations(items, layout.width, layout.height);
      if (mode === 'speed' && frontier && items.length) {
        const edge = speedFrontier(items);
        traces.push({type: 'scatter', mode: 'lines', name: '本轮质量与速度前沿',
          x: [0, ...edge.map(m => m.p50), maxSpeed], y: [edge[0].quality, ...edge.map(m => m.quality), edge.at(-1).quality],
          line: {color: css('text'), width: 1.5}, opacity: .7, hoverinfo: 'skip'});
      }
      for (const m of items) traces.push({type: 'scatter', mode: 'markers', name: m.name,
        x: [mode === 'speed' ? m.p50 : m.cost], y: [m.quality], ids: [m.id],
        marker: {size: focused === m.id ? 17 : 12, color: color(m), opacity: !focused || focused === m.id ? 1 : .5,
          line: {color: css('bg'), width: 2}}, customdata: [pointData(m)], hovertemplate: tooltip});
    }
    if (!items.length) layout.annotations = [{text: '选择模型后查看对比图', xref: 'paper', yref: 'paper',
      x: .5, y: .5, showarrow: false, font: {size: 16, color: css('muted')}}];
    await Plotly.react(chart, traces, layout, {displayModeBar: false, scrollZoom: true, responsive: false,
      doubleClick: 'reset', showTips: false});
    chart.querySelector('.chart-loading')?.remove();
    if (!ready) {
      chart.on('plotly_click', event => {
        const id = event.points?.[0]?.customdata?.[0];if (id) focusModel(id);
      });
      chart.on('plotly_relayout', event => {if (event['scene.camera']) camera = event['scene.camera'];});
      ready = true;
    }
    chart.dataset.mode = mode;chart.dataset.points = items.length;
    $('chart-error').hidden = true;
  }
  function render() {
    queue = queue.then(drawChart).catch(error => {
      console.error(error);$('chart-error').textContent = '图表暂时无法显示，可在下方查看完整数据。';
      $('chart-error').hidden = false;
    });
    return queue;
  }
  function selectMode(button) {
    mode = button.dataset.chartMode;
    document.querySelectorAll('[data-chart-mode]').forEach(tab => {
      const active = tab === button;tab.setAttribute('aria-selected', String(active));tab.tabIndex = active ? 0 : -1;
    });
    render();
  }
  const tabs = [...document.querySelectorAll('[data-chart-mode]')];
  tabs.forEach((button, index) => {
    button.onclick = () => selectMode(button);
    button.onkeydown = event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 :
        (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      selectMode(tabs[next]);tabs[next].focus();
    };
  });
  document.querySelectorAll('[data-model-toggle]').forEach(button => button.onclick = () => {
    const id = button.dataset.modelToggle;selected.has(id) ? selected.delete(id) : selected.add(id);
    syncModels();render();
  });
  $('models-all').onclick = () => {models.forEach(m => selected.add(m.id));syncModels();render();};
  $('frontier-toggle').onclick = () => {frontier = !frontier;$('frontier-toggle').setAttribute('aria-pressed', String(frontier));render();};
  $('chart-reset').onclick = () => {camera = defaultCamera();revision++;render();};
  function zoom(factor) {
    if (!ready) return;
    if (mode === 'value') {
      camera = {...camera, eye: Object.fromEntries(Object.entries(camera.eye).map(([axis, value]) => [axis, value * factor]))};
      Plotly.relayout(chart, {'scene.camera': camera});
    } else {
      const changes = {};
      for (const axis of ['xaxis', 'yaxis']) {
        const range = chart._fullLayout[axis].range, center = (range[0] + range[1]) / 2;
        changes[axis + '.range'] = range.map(value => center + (value - center) * factor);
      }
      Plotly.relayout(chart, changes);
    }
  }
  function rotate(angle) {
    if (!ready || mode !== 'value') return;
    const {x, y, z} = camera.eye;
    camera = {...camera, eye: {x: x * Math.cos(angle) - y * Math.sin(angle), y: x * Math.sin(angle) + y * Math.cos(angle), z}};
    Plotly.relayout(chart, {'scene.camera': camera});
  }
  $('chart-zoom-in').onclick = () => zoom(.82);$('chart-zoom-out').onclick = () => zoom(1.22);
  $('rotate-left').onclick = () => rotate(-Math.PI / 9);$('rotate-right').onclick = () => rotate(Math.PI / 9);
  chart.onkeydown = event => {
    if (['+', '=', '-', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Escape'].includes(event.key)) {
      if (event.key.startsWith('Arrow') && mode !== 'value') return;
      event.preventDefault();
      if (event.key === 'Escape') {Plotly.Fx.unhover(chart);return;}
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') rotate((event.key === 'ArrowLeft' ? -1 : 1) * Math.PI / 9);
      else if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
        camera = {...camera, eye: {...camera.eye, z: Math.max(.1, Math.min(3, camera.eye.z + (event.key === 'ArrowUp' ? .2 : -.2)))}};
        Plotly.relayout(chart, {'scene.camera': camera});
      } else zoom(event.key === '-' ? 1.22 : .82);
    }
  };
  document.querySelectorAll('[data-model-select]').forEach(button => button.onclick = () => focusModel(button.dataset.modelSelect));
  for (const button of table.querySelectorAll('[data-model-sort]')) button.onclick = () => {
    const header = button.closest('th'), key = button.dataset.modelSort;
    const ascending = header.getAttribute('aria-sort') === 'descending' ||
      (!header.hasAttribute('aria-sort') && ['name', 'p50', 'p95', 'cost'].includes(key));
    for (const th of table.querySelectorAll('th[aria-sort]')) th.removeAttribute('aria-sort');
    header.setAttribute('aria-sort', ascending ? 'ascending' : 'descending');
    const rows = [...table.tBodies[0].rows];
    rows.sort((a, b) => (key === 'name' ? a.dataset.name.localeCompare(b.dataset.name) :
      Number(a.dataset[key]) - Number(b.dataset[key])) * (ascending ? 1 : -1));
    table.tBodies[0].append(...rows);
    $('model-sort-status').textContent = '已按' + button.textContent.replace('↕', '').trim() + (ascending ? '升序' : '降序') + '排列';
  };
  new MutationObserver(() => {detail(models.find(m => m.id === focused));render();})
    .observe(document.documentElement, {attributes: true, attributeFilter: ['data-theme']});
  new ResizeObserver(() => {clearTimeout(resizeTimer);resizeTimer = setTimeout(render, 120);}).observe(chart);
  selectMode($('tab-' + (location.hash === '#3d' ? 'value' : 'speed')));
})();
