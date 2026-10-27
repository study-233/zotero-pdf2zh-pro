const {test} = require('node:test');
const assert = require('node:assert/strict');
const {searchEntries, legacyTarget} = require('./static/site.js');
const {speedFrontier, keyModels, hasUsdCost, costLabel} = require('./static/benchmark.js');

const records = [
  {title:'Windows 安装', section:'服务启动', text:'配置 API 之前先启动本地服务。', url:'/project/guide/windows/#start'},
  {title:'配置翻译 API', section:'测试 API', text:'填写 Key、地址、模型，再执行测试。', url:'/project/guide/api/#test'},
  {title:'失败补译', section:'任务状态', text:'保留已验证译文，只补译剩余段落。', url:'/project/guide/tasks/#retry'},
];
test('Chinese and case-insensitive multiword search reaches a precise section', () => {
  assert.equal(searchEntries(records, '补译')[0].url, records[2].url);
  assert.equal(searchEntries(records, 'api 测试')[0].url, records[1].url);
  assert.equal(searchEntries(records, 'API')[0].title, '配置翻译 API');
});
test('empty and unmatched queries produce no results', () => {
  assert.deepEqual(searchEntries(records, '   '), []);
  assert.deepEqual(searchEntries(records, '不存在的内容'), []);
});
test('legacy benchmark anchors preserve the GitHub Pages project base', () => {
  for (const hash of ['#results', '#method', '#compare']) {
    assert.equal(legacyTarget('/zotero-pdf2zh-pro/', hash), '/zotero-pdf2zh-pro/benchmark/' + hash);
  }
  assert.equal(legacyTarget('/zotero-pdf2zh-pro/', '#quick-start'), null);
  assert.equal(legacyTarget('/zotero-pdf2zh-pro/', ''), null);
});
test('speed frontier requires both quality and latency, with at least one strict improvement', () => {
  const fast = {id:'fast', p50:3, quality:97, cost:.2};
  const high = {id:'high', p50:4, quality:99, cost:.5};
  const tied = {id:'tied', p50:4, quality:99, cost:.01};
  const rows = [fast, high, tied, {id:'slower', p50:5, quality:98, cost:0},
    {id:'worse', p50:3, quality:96}, {id:'same-score', p50:7, quality:99}];
  assert.deepEqual(speedFrontier(rows).map(m => m.id), ['fast','high','tied']);
  assert.deepEqual(speedFrontier([]), []);
  assert.deepEqual(speedFrontier([fast]), [fast]);
});

test('subscription amounts stay unknown while measured zero USD remains comparable', () => {
  assert.equal(hasUsdCost({cost:null}), false);
  assert.equal(hasUsdCost({cost:NaN}), false);
  assert.equal(hasUsdCost({cost:0}), true);
  assert.equal(hasUsdCost({cost:.05}), true);
});

test('persistent labels keep the current frontier, both GPT sources and a selected ordinary point', () => {
  const rows = [
    {id:'fast', name:'Fast', p50:1, quality:95, cost:.3},
    {id:'best', name:'Best', p50:2, quality:99, cost:.2},
    {id:'cheap', name:'Cheap', p50:4, quality:94, cost:.01},
    {id:'ordinary', name:'Ordinary', p50:5, quality:93, cost:.4},
    {id:'command', name:'GPT-6 Luna · Command Code', p50:3, quality:98, cost:.2},
    {id:'account', name:'GPT-6 Luna · 插件账号', p50:4, quality:98, cost:.2},
  ];
  assert.deepEqual(keyModels(rows, 'speed').map(m => m.id), ['fast','best','command','account']);
  assert.deepEqual(keyModels(rows, 'cost').map(m => m.id), ['best','cheap','command','account']);
  assert.ok(keyModels(rows, 'speed', 'ordinary').includes(rows[3]));
  assert.equal(keyModels(rows, 'speed').includes(rows[3]), false);
});

test('API equivalent estimate is explicitly labeled in chart details', () => {
  assert.equal(costLabel({cost:.0487976, costEstimated:true}), '≈$0.04880（API 等值估算）');
  assert.equal(costLabel({cost:.049056325, costEstimated:false}), '$0.04906');
  assert.equal(costLabel({cost:null}), '套餐用量（无按次美元金额）');
});

test('missing usage receipts keep recorded cost visibly partial', () => {
  assert.equal(costLabel({cost:.12, costPartial:true, unknownUsageCalls:3}), '≥$0.12000（已记录部分；缺失 3 条用量回执）');
  assert.equal(costLabel({cost:.1, costEstimated:true, costPartial:true, unknownUsageCalls:1}), '≈≥$0.10000（API 等值估算；已记录部分；缺失 1 条用量回执）');
});
