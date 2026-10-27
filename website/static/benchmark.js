(() => {
  'use strict';
  function speedFrontier(models) {
    return models.filter(a => !models.some(b => b.p50 <= a.p50 && b.quality >= a.quality &&
      (b.p50 < a.p50 || b.quality > a.quality))).sort((a, b) => a.p50 - b.p50);
  }
  function keyModels(models, mode, focused) {
    const value = m => mode === 'cost' ? m.cost : m.p50;
    const edge = models.filter(a => !models.some(b => value(b) <= value(a) && b.quality >= a.quality &&
      (value(b) < value(a) || b.quality > a.quality)));
    return models.filter(m => edge.includes(m) || m.name.startsWith('GPT-') || m.id === focused);
  }
  const hasUsdCost = model => typeof model.cost === 'number' && Number.isFinite(model.cost);
  function costLabel(model) {
    if (!hasUsdCost(model)) return '套餐用量（无按次美元金额）';
    const notes = [];
    if (model.costEstimated) notes.push('API 等值估算');
    if (model.costPartial) notes.push('已记录部分；缺失 ' + model.unknownUsageCalls + ' 条用量回执');
    return (model.costEstimated ? '≈' : '') + (model.costPartial ? '≥' : '') + '$' +
      model.cost.toFixed(5) + (notes.length ? '（' + notes.join('；') + '）' : '');
  }
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {speedFrontier, keyModels, hasUsdCost, costLabel};
    return;
  }
  const $ = id => document.getElementById(id);
  const main = document.querySelector('.benchmark-explorer');
  if (!main) return;
  const table = $('model-comparison'), chart = $('benchmark-chart');
  const models = [...table.tBodies[0].rows].map(row => ({row, id: row.dataset.id, name: row.dataset.name,
    color: Number(row.dataset.color), quality: Number(row.dataset.quality), p50: Number(row.dataset.p50),
    p95: Number(row.dataset.p95), cost: row.dataset.cost === '' ? null : Number(row.dataset.cost), successes: Number(row.dataset.successes),
    samples: Number(row.dataset.samples), configuration: row.dataset.configuration, logo: row.dataset.logo,
    source: row.dataset.source, sourceLogo: row.dataset.sourceLogo,
    costEstimated: row.dataset.costEstimated === 'true', costPartial: row.dataset.costPartial === 'true', unknownUsageCalls: Number(row.dataset.unknownUsageCalls || 0)}))
    .sort((a, b) => a.color - b.color);
  const selected = new Set(models.map(m => m.id));
  const maxSpeed = Math.ceil(Math.max(...models.map(m => m.p50)) * 1.18 / 5) * 5;
  const maxCost = Math.ceil(Math.max(...models.filter(hasUsdCost).map(m => m.cost)) * 1.18 / .05) * .05;
  const qualityRange = Object.freeze([90, 101]);
  const defaultCamera = () => ({eye: chart.clientWidth < 600 ? {x: 2.15, y: 2.35, z: 1.45} :
    {x: 1.35, y: 1.5, z: 1.0}, center: {x: 0, y: 0, z: -.1}});
  let view = 'speed', dragMode = 'pan';
  let mode = 'speed', frontier = true, focused = null, camera = defaultCamera(), revision = 0;
  let queue = Promise.resolve(), ready = false, resizeTimer;
  const color = m => getComputedStyle(main).getPropertyValue('--model-' + m.color).trim();
  const css = key => getComputedStyle(document.documentElement).getPropertyValue('--' + key).trim();
  const visible = () => models.filter(m => selected.has(m.id));
  const pointData = m => [m.id, m.quality, m.p50, m.p95, m.cost, m.successes, m.samples, costLabel(m), m.configuration, m.source];
  const hoverCard = document.createElement('div');hoverCard.id = 'model-hover-card';
  hoverCard.className = 'model-hover-card';hoverCard.hidden = true;hoverCard.setAttribute('role', 'tooltip');
  chart.append(hoverCard);
  let hideCardTimer, cardModel = null;
  const make = (tag, className, text) => {
    const node = document.createElement(tag);node.className = className;
    if (text !== undefined) node.textContent = text;return node;
  };
  function hideCard() {clearTimeout(hideCardTimer);hoverCard.hidden = true;cardModel = null;}
  const deferHideCard = () => {clearTimeout(hideCardTimer);hideCardTimer = setTimeout(() => {
    const pinned = models.find(m => m.id === focused);
    if (pinned && view !== 'table') showCard(pinned, pointNode(pinned.id));else hideCard();
  }, 160);};
  function showCard(model, anchor) {
    clearTimeout(hideCardTimer);
    if (cardModel !== model.id) {
      hoverCard.replaceChildren();cardModel = model.id;hoverCard.dataset.model = model.id;
      const heading = make('div', 'hover-card-heading');
      for (const url of [...new Set([model.sourceLogo, model.logo])]) {
        const logo = make('img', 'hover-card-logo');logo.src = url;logo.alt = '';heading.append(logo);
      }
      heading.append(make('strong', '', model.name.split(' · ')[0]));hoverCard.append(heading);
      hoverCard.append(make('div', 'hover-card-source', model.source),
        make('div', 'hover-card-configuration', model.configuration));
      const metrics = make('div', 'hover-card-metrics');
      const amount = (model.costEstimated ? '≈' : '') + (model.costPartial ? '≥' : '') + '$' + model.cost?.toFixed(5);
      const primary = mode === 'speed' ? ['P50 耗时', model.p50.toFixed(2), ' s'] :
        ['测评费用', hasUsdCost(model) ? amount : '未知', ''];
      for (const [label, value, unit] of [primary, ['翻译质量', model.quality.toFixed(2), ' / 100']]) {
        const metric = make('div', 'hover-card-metric'), number = make('strong', '', value);
        number.append(make('small', '', unit));metric.append(make('span', '', label), number);metrics.append(metric);
      }
      hoverCard.append(metrics);
      const secondary = make('div', 'hover-card-secondary');
      for (const [label, value] of [['P95', model.p95.toFixed(2) + ' s'],
        ['成功', model.successes + ' / ' + model.samples],
        mode === 'speed' ? ['费用', hasUsdCost(model) ? amount : '未知'] : ['P50', model.p50.toFixed(2) + ' s']]) {
        const item = make('span', '', label);item.append(make('strong', '', value));secondary.append(item);
      }
      hoverCard.append(secondary);
      if (model.costEstimated) hoverCard.append(make('p', 'hover-card-note', '费用为 API 等值估算，非套餐实际扣费'));
      else if (model.costPartial) hoverCard.append(make('p', 'hover-card-note', '费用仅含已记录部分；缺失 ' + model.unknownUsageCalls + ' 条用量回执'));
      const source = make('a', 'hover-card-link', '查看测评来源');source.href = '#method';
      source.onclick = () => {$('method').open = true;hideCard();};hoverCard.append(source);
    }
    hoverCard.hidden = false;
    const origin = chart.getBoundingClientRect(), box = anchor?.getBoundingClientRect();
    const x = box ? box.left + box.width / 2 - origin.left : chart.clientWidth / 2;
    const y = box ? box.top + box.height / 2 - origin.top : chart.clientHeight / 2;
    const gap = 16, width = hoverCard.offsetWidth, height = hoverCard.offsetHeight;
    const left = x + gap + width < chart.clientWidth - 10 ? x + gap : x - width - gap;
    const minTop = Math.max(8, 8 - origin.top);
    const maxTop = Math.min(chart.clientHeight - height - 8, innerHeight - origin.top - height - 12);
    Object.assign(hoverCard.style, {left: Math.max(10, Math.min(chart.clientWidth - width - 10, left)) + 'px',
      top: Math.max(minTop, Math.min(maxTop, y + gap)) + 'px'});
  }
  hoverCard.onmouseenter = () => clearTimeout(hideCardTimer);hoverCard.onmouseleave = deferHideCard;
  const pointNode = id => {
    const index = chart.data?.findIndex(trace => trace.ids?.[0] === id);
    return chart.querySelectorAll('.scatterlayer .trace')[index]?.querySelector('.point');
  };

  function detail(model) {
    const target = $('chart-selection');
    target.replaceChildren();
    if (!model) {
      const hint = document.createElement('span');hint.className = 'selection-placeholder';
      hint.textContent = '悬停数据点查看详情 · 点选可固定';target.append(hint);return;
    }
    const name = document.createElement('span');name.className = 'selection-name';
    name.style.setProperty('--model-color', color(model));
    const logo = document.createElement('img');logo.className = 'model-logo';logo.src = model.logo;logo.alt = '';
    name.append(logo, document.createTextNode(model.name));target.append(name);
    const source = document.createElement('span');source.className = 'selection-source';
    const sourceLogo = document.createElement('img');sourceLogo.className = 'source-logo';sourceLogo.src = model.sourceLogo;sourceLogo.alt = '';
    const sourceName = document.createElement('strong');sourceName.textContent = model.source;
    source.append(document.createTextNode('来源'), sourceLogo, sourceName);target.append(source);
    target.append(make('span', '', '已固定详情 · Esc 取消'));
  }
  function focusModel(id) {
    if (id && view === 'table') selected.add(id);
    focused = focused === id ? null : id;
    for (const m of models) {
      const active = m.id === focused;m.row.dataset.selected = String(active);
      m.row.querySelector('[data-model-select]').setAttribute('aria-pressed', String(active));
    }
    detail(models.find(m => m.id === focused));
    render().then(() => {const model = models.find(m => m.id === focused);
      if (model && view !== 'table') showCard(model, pointNode(model.id));else hideCard();});
  }
  function syncModels() {
    for (const m of models) m.row.hidden = !selected.has(m.id);
    document.querySelectorAll('[data-model-toggle]').forEach(button =>
      button.setAttribute('aria-pressed', String(selected.has(button.dataset.modelToggle))));
    $('visible-model-count').textContent = selected.size;
    $('models-all').hidden = selected.size === models.length;
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
    const margins = chartMargins();
    const plotWidth = width - margins.l - margins.r, plotHeight = height - margins.t - margins.b;
    const points = items.map(m => ({x: (1 - (mode === 'speed' ? m.p50 : m.cost) / maximum) * plotWidth,
      y: (qualityRange[1] - m.quality) / (qualityRange[1] - qualityRange[0]) * plotHeight}));
    function penalty(box) {
      const overflow = Math.max(0, -box.left) + Math.max(0, box.left + box.w - plotWidth) +
        Math.max(0, -box.top) + Math.max(0, box.top + box.h - plotHeight);
      const labels = boxes.filter(b => box.left < b.left + b.w + 6 && box.left + box.w + 6 > b.left &&
        box.top < b.top + b.h + 6 && box.top + box.h + 6 > b.top).length;
      const markers = points.filter(p => box.left < p.x + 17 && box.left + box.w > p.x - 17 &&
        box.top < p.y + 17 && box.top + box.h > p.y - 17).length;
      return labels * 1e7 + markers * 1e6 + overflow * 10000;
    }
    const measure = document.createElement('canvas').getContext('2d');
    measure.font = size + 'px ' + css('font-benchmark');
    const keys = keyModels(items, mode, focused);
    const ordered = mobile ? [...keys].sort((a, b) => b.quality - a.quality) : keys;
    return ordered.map((m, index) => {
      const px = (1 - (mode === 'speed' ? m.p50 : m.cost) / maximum) * plotWidth;
      const py = (qualityRange[1] - m.quality) / (qualityRange[1] - qualityRange[0]) * plotHeight;
      const text = '\u00a0'.repeat(m.sourceLogo === m.logo ? 6 : 12) + m.name;
      const w = measure.measureText(text).width + 12, h = size + 14;
      if (mobile) {
        const target = (index + .6) / (ordered.length + .2) * plotHeight;
        const candidates = [];
        for (let delta = -60; delta <= 60; delta += 4) {
          const box = {left: 4, top: target + delta - h / 2, w, h};
          candidates.push({box, delta, score: penalty(box) + Math.abs(delta)});
        }
        candidates.sort((a, b) => a.score - b.score);const best = candidates[0];boxes.push(best.box);
        return {x: mode === 'speed' ? m.p50 : m.cost, y: m.quality, text,
          ax: 4 - px, ay: target + best.delta - py,
          xanchor: 'left', yanchor: 'middle', showarrow: true, arrowhead: 0,
          arrowcolor: color(m), arrowwidth: 1, bgcolor: css('raised'), bordercolor: '#88929e',
          borderpad: 5, font: {size, color: css('text')}, captureevents: false};
      }
      const choices = [];
      for (const distance of [20, 48, 78, 108, 140]) for (const offset of [22, 56, 104, 160, 220])
        for (const side of [-1, 1]) for (const direction of [-1, 1]) {
        const dx = side * offset, dy = direction * distance;
        const box = {left: px + dx - (side < 0 ? w : 0), top: py + dy - h / 2, w, h};
        choices.push({box, dx, dy, side,
          score: penalty(box) + Math.hypot(offset, distance)});
      }
      choices.sort((a, b) => a.score - b.score);const best = choices[0];boxes.push(best.box);
      return {x: mode === 'speed' ? m.p50 : m.cost, y: m.quality, text,
        ax: best.dx, ay: best.dy, xanchor: best.side < 0 ? 'right' : 'left', yanchor: 'middle',
        showarrow: true, arrowhead: 0, arrowwidth: 1, arrowcolor: color(m),
        bgcolor: css('raised'), bordercolor: '#88929e', borderwidth: 1, borderpad: 5,
        font: {size, color: css('text')}, captureevents: false};
    });
  }
  const chartMargins = () => ({l: chart.clientWidth < 600 ? 50 : 62, r: 22, t: 26, b: 58});
  function syncBrandBadges() {
    const active = document.activeElement?.closest('.brand-point')?.dataset.modelBrand;
    chart.querySelector('.brand-point-layer')?.remove();
    chart.querySelector('.annotation-logo-layer')?.remove();
    if (view === 'table' || mode === 'value' || !chart._fullLayout?.xaxis) return;
    const {xaxis, yaxis} = chart._fullLayout;
    const items = visible().filter(m => mode === 'speed' || hasUsdCost(m));
    const edge = mode === 'speed' && frontier ? speedFrontier(items) : [];
    const labels = document.createElement('div');labels.className = 'annotation-logo-layer';
    const origin = chart.getBoundingClientRect(), size = chart.clientWidth < 600 ? 12 : 14;
    for (const node of chart.querySelectorAll('.annotation')) {
      const m = items.find(model => model.name === node.querySelector('.annotation-text')?.textContent.trim());
      const background = node.querySelector('.bg');if (!m || !background) continue;
      const bounds = background.getBoundingClientRect();
      for (const [index, url] of (m.sourceLogo === m.logo ? [m.logo] : [m.sourceLogo, m.logo]).entries()) {
        const image = document.createElement('img');image.src = url;image.alt = '';
        image.dataset.logoKind = index === 0 ? 'source' : 'model';
        Object.assign(image.style, {left: bounds.left - origin.left + 5 + index * (size + 4) + 'px',
          top: bounds.top - origin.top + (bounds.height - size) / 2 + 'px', width: size + 'px', height: size + 'px'});
        labels.append(image);
      }
    }
    chart.append(labels);
    const layer = document.createElement('div');layer.className = 'brand-point-layer';
    Object.assign(layer.style, {left: xaxis._offset + 'px', top: yaxis._offset + 'px',
      width: xaxis._length + 'px', height: yaxis._length + 'px'});
    const traceNodes = chart.querySelectorAll('.scatterlayer .trace');
    for (const m of items) {
      const curveNumber = chart.data.findIndex(trace => trace.ids?.[0] === m.id);
      const node = traceNodes[curveNumber]?.querySelector('.point');
      if (node) {
        node.setAttribute('tabindex', '0');node.setAttribute('role', 'button');
        node.setAttribute('aria-label', m.name + '，来源 ' + m.source + '，查看测评数据');
        node.onfocus = () => showCard(m, node);node.onblur = deferHideCard;
        node.onkeydown = event => {if (event.key === 'Enter' || event.key === ' ') {event.preventDefault();focusModel(m.id);}};
      }
      if (!edge.includes(m) && m.id !== focused) continue;
      const point = node?.getBoundingClientRect();
      const x = point ? point.left + point.width / 2 - origin.left - xaxis._offset :
        xaxis.l2p(mode === 'speed' ? m.p50 : m.cost);
      const y = point ? point.top + point.height / 2 - origin.top - yaxis._offset : yaxis.l2p(m.quality);
      if (x < 0 || y < 0 || x > xaxis._length || y > yaxis._length) continue;
      const button = document.createElement('button');button.type = 'button';button.className = 'brand-point';
      button.dataset.modelBrand = m.id;button.dataset.frontier = String(edge.includes(m));
      button.dataset.focused = String(m.id === focused);
      button.setAttribute('aria-label', m.name + '，来源 ' + m.source + '，查看测评数据');
      Object.assign(button.style, {left: x + 'px', top: y + 'px', opacity: !focused || focused === m.id ? '1' : '.5'});
      button.style.setProperty('--model-color', color(m));
      const logo = document.createElement('img');logo.src = m.sourceLogo;logo.alt = '';
      button.append(logo);button.onclick = () => focusModel(m.id);
      const hover = () => showCard(m, button);
      button.onmouseenter = button.onfocus = hover;
      button.onmouseleave = button.onblur = deferHideCard;
      layer.append(button);
    }
    chart.append(layer);
    if (active) [...layer.children].find(button => button.dataset.modelBrand === active)?.focus({preventScroll:true});
  }
  function baseLayout() {
    return {width: chart.clientWidth, height: chart.clientHeight, paper_bgcolor: css('panel'),
      plot_bgcolor: css('panel'), font: {family: css('font-benchmark'), color: css('muted'), size: 13},
      showlegend: false, hoverlabel: {bgcolor: css('raised'), bordercolor: css('line'),
        font: {size: 13, color: css('text')}}, uirevision: mode + revision,
      margin: chartMargins()};
  }
  async function drawChart() {
    if (view === 'table') return;
    const items = visible().filter(m => mode === 'speed' || hasUsdCost(m)), layout = baseLayout(), traces = [];
    const is3d = mode === 'value', small = chart.clientWidth < 600;
    $('frontier-toggle').hidden = mode !== 'speed';
    $('drag-controls').hidden = is3d;
    $('chart-panel').setAttribute('aria-labelledby', 'tab-' + mode);
    $('chart-metric').textContent = is3d ? '费用（含 API 等值估算）× P50 耗时 × 翻译质量' : '翻译质量 / 100';
    $('chart-gesture').textContent = is3d ? '拖动旋转 · 双指缩放' :
      dragMode === 'zoom' ? '框选缩放 · 双击重置' : '拖动平移 · 滚轮缩放';
    chart.setAttribute('aria-label', is3d ? '价格、速度与质量三维图，方向键旋转，加减键缩放' :
      '质量与' + (mode === 'speed' ? '速度' : '费用') + '交互图，加减键缩放');
    if (is3d) {
      layout.margin = {l: small ? 20 : 0, r: small ? 20 : 0, t: 15, b: small ? 20 : 0};
      const axis = (title, range, tickformat) => ({title: {text: title, font: {size: small ? 10 : 12}},
        range, tickformat, tickfont: {size: small ? 9 : 11}, gridcolor: css('plot-grid'), zerolinecolor: css('line'),
        showbackground: true, backgroundcolor: css('panel'), color: css('muted'), showspikes: false, nticks: 5});
      layout.scene = {camera, dragmode: 'orbit', aspectmode: 'manual',
        aspectratio: small ? {x: 1, y: 1, z: 1.1} : {x: 1.25, y: 1.25, z: .95},
        xaxis: axis('费用 / USD', [0, maxCost], '$.2f'),
        yaxis: axis('P50 耗时 / s', [0, maxSpeed], '.0f'),
        zaxis: axis('翻译质量 / 100', [90, 101], '.0f'), annotations: []};
      for (const m of items) {
        traces.push({type: 'scatter3d', mode: 'lines', x: [m.cost, m.cost], y: [m.p50, m.p50],
          z: [90, m.quality], line: {color: color(m), width: 2, dash: 'dot'}, opacity: .28, hoverinfo: 'skip', showlegend: false});
        traces.push({type: 'scatter3d', mode: 'markers', name: m.name, x: [m.cost], y: [m.p50], z: [m.quality],
          ids: [m.id],
          marker: {symbol: m.costEstimated ? 'diamond-open' : 'circle', size: focused === m.id ? 9 : 6, color: color(m), opacity: !focused || focused === m.id ? 1 : .5,
            line: {color: css('bg'), width: 1}}, customdata: [pointData(m)], hoverinfo: 'none'});
        if (focused === m.id) layout.scene.annotations.push({x: m.cost, y: m.p50, z: m.quality,
          text: m.name, ax: -20, ay: -28, xanchor: 'right', showarrow: true,
          arrowhead: 0, arrowcolor: color(m), bgcolor: css('raised'), bordercolor: '#88929e',
          borderpad: 5, font: {size: small ? 11 : 13, color: css('text')}});
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
      layout.dragmode = dragMode;
      layout.xaxis = {title: {text: mode === 'speed' ? 'P50 耗时 / s　·　更快 →' : '费用 / USD　·　更低 →',
        font: {size: small ? 11 : 13}, standoff: 20}, range: [mode === 'speed' ? maxSpeed : maxCost, 0],
        gridcolor: css('plot-grid'), zeroline: false, tickformat: mode === 'cost' ? '$.2f' : '.0f', nticks: small ? 5 : 8,
        tickfont: {size: small ? 11 : 13}, fixedrange: false};
      // Plotly mutates supplied range arrays while zooming; keep the default intact.
      layout.yaxis = {title: {text: '翻译质量 / 100', font: {size: small ? 10 : 12}, standoff: 10}, range: [...qualityRange], gridcolor: css('plot-grid'), zeroline: false, dtick: 2,
        tickfont: {size: small ? 11 : 13}};
      layout.annotations = annotations(items, layout.width, layout.height);
      if (mode === 'speed' && frontier && items.length) {
        const edge = speedFrontier(items);
        traces.push({type: 'scatter', mode: 'lines', name: '本轮质量与速度前沿',
          x: [0, ...edge.map(m => m.p50), maxSpeed], y: [edge[0].quality, ...edge.map(m => m.quality), edge.at(-1).quality],
          line: {color: css('text'), width: 1.7}, opacity: .9, hoverinfo: 'skip'});
      }
      for (const m of items) traces.push({type: 'scatter', mode: 'markers', name: m.name,
        x: [mode === 'speed' ? m.p50 : m.cost], y: [m.quality], ids: [m.id],
        marker: {symbol: mode === 'cost' && m.costEstimated ? 'diamond-open' : 'circle', size: focused === m.id ? 17 : (mode === 'speed' && speedFrontier(items).includes(m) ? 15 : 10), color: color(m), opacity: !focused || focused === m.id ? .9 : .4,
          line: {color: css('panel'), width: 1.5}}, customdata: [pointData(m)], hoverinfo: 'none'});
    }
    if (!items.length) layout.annotations = [{text: '选择模型后查看对比图', xref: 'paper', yref: 'paper',
      x: .5, y: .5, showarrow: false, font: {size: 16, color: css('muted')}}];
    await Plotly.react(chart, traces, layout, {displayModeBar: false, scrollZoom: true, responsive: false,
      doubleClick: 'reset', showTips: false});
    syncBrandBadges();
    if (!hoverCard.isConnected) chart.append(hoverCard);
    chart.querySelector('.chart-loading')?.remove();
    if (!ready) {
      chart.on('plotly_click', event => {
        const id = event.points?.[0]?.customdata?.[0];if (id) focusModel(id);
      });
      chart.on('plotly_hover', event => {
        const point = event.points?.[0], model = models.find(m => m.id === point?.customdata?.[0]);
        if (model) showCard(model, pointNode(model.id) || (point?.bbox ? {getBoundingClientRect: () => {
          const origin = chart.getBoundingClientRect(), box = point.bbox;
          return {left: origin.left + box.x0, top: origin.top + box.y0, width: box.x1 - box.x0, height: box.y1 - box.y0};
        }} : null));
      });
      chart.on('plotly_unhover', deferHideCard);
      chart.on('plotly_relayout', event => {
        if (event['scene.camera']) camera = event['scene.camera'];hideCard();syncBrandBadges();
      });
      chart.on('plotly_relayouting', syncBrandBadges);
      ready = true;
    }
    chart.dataset.mode = mode;chart.dataset.points = items.length;
    $('chart-error').hidden = true;
  }
  function render() {
    hideCard();
    queue = queue.then(drawChart).catch(error => {
      console.error(error);$('chart-error').textContent = '图表暂时无法显示，可在下方查看完整数据。';
      $('chart-error').hidden = false;
    });
    return queue;
  }
  function selectMode(button) {
    hideCard();
    view = button.dataset.chartMode;
    document.querySelectorAll('[data-chart-mode]').forEach(tab => {
      const active = tab === button;tab.setAttribute('aria-selected', String(active));tab.tabIndex = active ? 0 : -1;
    });
    const isTable = view === 'table';
    $('compare').hidden = !isTable;
    $('chart-panel').hidden = $('chart-tools').hidden = $('chart-legend').hidden = isTable;
    main.dataset.view = view;
    history.replaceState(null, '', '#view=' + view);
    if (isTable) {
      models.forEach(model => {model.row.hidden = false;});$('table-empty').hidden = true;
    } else {mode = view;syncModels();render();}
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
  document.querySelectorAll('[data-drag-mode]').forEach(button => button.onclick = () => {
    dragMode = button.dataset.dragMode;
    document.querySelectorAll('[data-drag-mode]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    render();
  });
  let copyTimer;
  document.querySelector('.benchmark-heading a[href="#method"]').onclick = () => {$('method').open = true;};
  $('copy-view').onclick = async () => {
    const status = $('copy-status');clearTimeout(copyTimer);
    try {const url = new URL(location.href);url.hash = 'view=' + view;await navigator.clipboard.writeText(url.href);status.textContent = '已复制当前视图链接';}
    catch {status.textContent = '复制失败，请复制浏览器地址';}
    status.hidden = false;copyTimer = setTimeout(() => {status.hidden = true;}, 2400);
  };
  $('models-all').onclick = () => {models.forEach(m => selected.add(m.id));syncModels();render();};
  $('frontier-toggle').onclick = () => {frontier = !frontier;$('frontier-toggle').setAttribute('aria-pressed', String(frontier));render();};
  $('chart-reset').onclick = () => {camera = defaultCamera();revision++;focusModel(null);};
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
  chart.onkeydown = event => {
    if (['+', '=', '-', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Escape'].includes(event.key)) {
      if (event.key.startsWith('Arrow') && mode !== 'value') return;
      event.preventDefault();
      if (event.key === 'Escape') {Plotly.Fx.unhover(chart);focusModel(null);return;}
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
    rows.sort((a, b) => {
      if (key === 'cost' && (a.dataset.cost === '' || b.dataset.cost === ''))
        return Number(a.dataset.cost === '') - Number(b.dataset.cost === '');
      return (key === 'name' ? a.dataset.name.localeCompare(b.dataset.name) :
        Number(a.dataset[key]) - Number(b.dataset[key])) * (ascending ? 1 : -1);
    });
    table.tBodies[0].append(...rows);
    $('model-sort-status').textContent = '已按' + button.textContent.replace('↕', '').trim() + (ascending ? '升序' : '降序') + '排列';
  };
  new MutationObserver(() => {detail(models.find(m => m.id === focused));render();})
    .observe(document.documentElement, {attributes: true, attributeFilter: ['data-theme']});
  let compact = chart.clientWidth < 600;
  new ResizeObserver(() => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (compact !== (chart.clientWidth < 600)) {
        compact = chart.clientWidth < 600;camera = defaultCamera();revision++;
      }
      render();
    }, 120);
  }).observe(chart);
  const requested = new URLSearchParams(location.hash.slice(1)).get('view') ||
    (location.hash === '#3d' ? 'value' : location.hash === '#compare' ? 'table' : 'speed');
  selectMode($('tab-' + (['speed','cost','value','table'].includes(requested) ? requested : 'speed')));
  document.fonts.ready.then(render);
})();
