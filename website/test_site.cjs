const {test} = require('node:test');
const assert = require('node:assert/strict');
const {searchEntries, legacyTarget} = require('./static/site.js');

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
