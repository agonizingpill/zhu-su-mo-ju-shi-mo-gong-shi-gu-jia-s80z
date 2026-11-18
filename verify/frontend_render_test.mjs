// 前端渲染冒烟：通过 loader 桩注入依赖，导入真实 risk.js / deliveries.js 并渲染。
// 启动：node --input-type=module 或直接 node frontend_render_test.mjs
import {pathToFileURL} from 'node:url';
import {readFile} from 'node:fs/promises';
import Path from 'node:path';

const PAGES = Path.resolve('/workspace/frontend/src/pages');
let pass = 0, fail = 0;
const check = (name, cond, detail = '') => {
  if (cond) { pass++; console.log('  PASS ' + name); }
  else { fail++; console.log('  FAIL ' + name + ' ' + detail); }
};

const stubs = {
  esc: x => String(x ?? ''),
  number: x => x == null ? '—' : Number(x).toFixed(2),
  money: x => Number(x || 0).toFixed(2),
  notify: () => {},
  empty: m => '<empty>' + m,
  send: async () => ({}),
};
const RISK_REVIEW_STATES = {pending: '待复核', insufficient: '样本不足拦截', blocked: '风险拦截',
  released: '已放行', dispatched: '已发样', voided: '旧许可作废'};
const RISK_TABS = [['pending', '待复核'], ['blocked', '风险拦截'], ['released', '已放行'], ['dispatched', '已发样']];
const DELIVERY_STATES = {dispatched: '已发样待签收', received: '客户已签收', accepted: '客户接受',
  rectification: '需整改重试', closed: '主管已关闭'};

// 读取源文件，把相对 import 替换为内联桩对象，再以 data URL 动态导入
async function loadPage(file, extra) {
  let src = await readFile(Path.join(PAGES, file), 'utf8');
  src = src.replace(/import\{[^}]*\}from'[^']*';?/g, '');   // 去掉行内 ESM 导入
  const preamble = 'const {' + Object.keys({...stubs, ...extra}).join(',') + '}=globalThis.__stub;';
  const blob = preamble + '\n' + src.replace(/export function/g, 'function') +
    '\nglobalThis.__mod={render,bind};';
  globalThis.__stub = {...stubs, ...extra};
  await import('data:text/javascript;base64,' + Buffer.from(blob).toString('base64'));
  return globalThis.__mod;
}

const risk = await loadPage('risk.js', {RISK_REVIEW_STATES, RISK_TABS});
const store = {
  user: {role: 'admin'}, riskTab: 'pending', riskSelected: null,
  reviews: [{id: 1, status: 'signed', estimate_code: 'QE-1', execution_id: 11}],
  riskReviews: [
    {id: 1, state: 'blocked', review_id: 1, mould_id: 7, mould_code: 'MD-86', estimate_code: 'QE-1',
      threshold: 0.5, reject_probability: 0.83, attempts: 1, initiated_by: 'estimator@ruimo.demo',
      release_note: '', released_by: null, released_at: null, dispatched_at: null,
      voided_reason: '', voided_at: '',
      basis: {sufficient: true, reject_probability: 0.83, verdict: 'blocked', training_total: 27,
        mould_samples: 9, positive_rectification: 11, feature_names: ['cavities', 'good_rate'],
        features: [8, 0.94], coefficients: [{name: 'cavities', coefficient: 0.31}, {name: 'good_rate', coefficient: -0.22}],
        group_validation: {splits: 3, samples: 27, accuracy: 0.81, precision: 0.8, recall: 0.7, auc: 0.86}}},
    {id: 2, state: 'insufficient', review_id: 2, mould_id: 8, mould_code: 'MD-42', estimate_code: 'QE-2',
      threshold: 0.5, reject_probability: null, attempts: 2, initiated_by: 'estimator@ruimo.demo',
      release_note: '', released_by: null, released_at: null, dispatched_at: null, voided_reason: '', voided_at: '',
      basis: {sufficient: false, reason: '样本不足', training_total: 3, min_total_required: 20,
        mould_samples: 1, min_mould_required: 6}},
    {id: 3, state: 'released', review_id: 3, mould_id: 9, mould_code: 'MD-115', estimate_code: 'QE-3',
      threshold: 0.5, reject_probability: 0.12, attempts: 1, initiated_by: 'estimator@ruimo.demo',
      release_note: '不接受概率 0.12 低于阈值 0.5，自动通过', released_by: 'estimator@ruimo.demo',
      released_at: '2026-11-18', dispatched_at: null, voided_reason: '', voided_at: '',
      basis: {sufficient: true, training_total: 27, mould_samples: 9, feature_names: [], features: [],
        group_validation: {splits: 3}}},
  ],
  riskThreshold: {threshold: 0.5, updated_by: 'admin@ruimo.demo', updated_at: '2026-11-18'},
};

console.log('== 风险复核页渲染 ==');
const html = risk.render(store);
for (const token of ['验收风险复核', '风险拦截', '样本不足拦截', '已放行', '主管拦截阈值',
  '不接受概率', '按模具分组留出验证', '发起 / 重试复核', '主管说明后放行']) {
  check('含文案 ' + token, html.includes(token));
}
const viewerStore = {...store, user: {role: 'viewer'}};
const htmlViewer = risk.render(viewerStore);
check('viewer 发起按钮禁用', htmlViewer.includes('disabled'));
const tabStore = {...store, riskTab: 'dispatched'};
check('已发样分栏可渲染', risk.render(tabStore).includes('验收风险复核'));

console.log('== 客户交付页风险门联动 ==');
const deliv = await loadPage('deliveries.js', {DELIVERY_STATES, RISK_REVIEW_STATES});
const dStore = {
  user: {role: 'estimator'}, deliverySelected: null,
  reviews: [{id: 1, status: 'signed', estimate_code: 'QE-1', execution_id: 11},
            {id: 2, status: 'signed', estimate_code: 'QE-2', execution_id: 12}],
  deliveries: [],
  riskReviews: [
    {id: 1, review_id: 1, state: 'released', reject_probability: 0.12, threshold: 0.5},
    {id: 2, review_id: 2, state: 'blocked', reject_probability: 0.83, threshold: 0.5},
  ],
};
const htmlD = deliv.render(dStore);
check('已放行结算可选发样', htmlD.includes('已放行可发样'));
check('未放行结算被禁用', htmlD.includes('未通过风险门，不可发样') && htmlD.includes('<option value="2"'));
check('提示先过风险门', htmlD.includes('风险复核'));

console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
process.exit(fail ? 1 : 0);
