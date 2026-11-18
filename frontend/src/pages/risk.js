import{send}from'../api.js';import{esc,number,money}from'../utils/format.js';import{notify,empty}from'../components/common.js';import{RISK_REVIEW_STATES,RISK_TABS}from'../constants.js';

const stateClass={pending:'requested',insufficient:'failed',blocked:'failed',released:'committed',dispatched:'committed',voided:'failed'};
const badge=x=>`<span class="status ${stateClass[x.state]||''}">${RISK_REVIEW_STATES[x.state]||x.state}</span>`;

function basisBlock(r){
  const b=r.basis||{};
  if(!b.sufficient){
    return `<div class="rule">样本不足拦截：${esc(b.reason||'已关闭的同模具客户意见不足，未给出概率')}<br>训练意见 ${b.training_total??0}/${b.min_total_required??20}，同模具 ${b.mould_samples??0}/${b.min_mould_required??6}。积累新的已签收关闭意见后可重试，重试不新增复核。</div>`;
  }
  const gv=b.group_validation||{};
  return `<details class="basis" open><summary>复核依据（复核当时固化，后补意见不混入）</summary>
    <div class="ribbon">
      <div>不接受概率<strong class="mono">${number(r.reject_probability)}</strong></div>
      <div>主管阈值<strong class="mono">${number(r.threshold)}</strong></div>
      <div>结论<strong>${b.verdict==='blocked'?'达到阈值拦截':'低于阈值通过'}</strong></div>
      <div>训练意见<strong class="mono">${b.training_total} 条</strong></div>
      <div>同模样本<strong class="mono">${b.mould_samples} 条</strong></div>
    </div>
    <p class="rule">按模具分组留出验证（LeaveOneGroupOut）：${gv.splits?`${gv.splits} 折 / ${gv.samples} 样本，准确率 ${number(gv.accuracy)}，精确率 ${number(gv.precision)}，召回率 ${number(gv.recall)}${gv.auc!=null?`，AUC ${number(gv.auc)}`:''}`:'分组不足，未产出分组指标'}</p>
    <table class="tablewrap"><thead><tr><th>特征</th><th>本次取值</th><th>标准化系数</th></tr></thead><tbody>
      ${(b.feature_names||[]).map((n,i)=>{const c=(b.coefficients||[]).find(x=>x.name===n);return `<tr><td>${esc(n)}</td><td class="mono">${number((b.features||[])[i])}</td><td class="mono">${c?number(c.coefficient):'—'}</td></tr>`}).join('')}
    </tbody></table>
    <p class="rule">训练集只含已签收并关闭的首次客户意见：整改 ${b.positive_rectification} 条、接受 ${b.training_total-b.positive_rectification} 条。估价工时回归模型与本分类器相互独立，未受影响。</p>
  </details>`;
}

export function render(s){
  const tab=s.riskTab||'pending';
  const inTab=s.riskReviews.filter(x=>tab==='pending'?['pending','insufficient','blocked','voided'].includes(x.state):x.state===tab||(tab==='released'&&x.state==='released'));
  const cur=s.riskReviews.find(x=>x.id===Number(s.riskSelected))||inTab[0]||s.riskReviews[0];
  const used=new Set(s.riskReviews.filter(x=>x.state!=='voided').map(x=>x.review_id));
  const candidates=s.reviews.filter(x=>x.status==='signed'&&!used.has(x.id));
  const counts=Object.fromEntries(RISK_TABS.map(([k])=>[k,s.riskReviews.filter(x=>k==='pending'?['pending','insufficient','blocked','voided'].includes(x.state):x.state===k).length]));
  return `<div class="pagehead"><div class="kicker">10 / ACCEPTANCE RISK GATE</div><h1>验收风险复核</h1><p>同模反复整改不得再直接发样。用已签收的首次客户意见训练分类器，按模具分组验证给出不接受概率，主管设阈值、样本不足拦截；高风险须主管说明放行。</p></div>
  <div class="risk-layout">
    <section class="section">
      <h2>发起复核</h2>
      <form id="risk-create">
        <label>已签结实绩（结算）<select name="review_id">${candidates.map(x=>`<option value="${x.id}">${esc(x.estimate_code)} / 结算${x.id}</option>`).join('')||'<option value="">暂无可复核结算</option>'}</select></label>
        <button class="primary" ${s.user.role==='viewer'||!candidates.length?'disabled':''}>发起 / 重试复核</button>
      </form>
      <p class="rule">中断后可重试，复用同一条复核且不新增记录；复核固定当时实绩与已关闭意见，后补意见不混入。</p>
      <div class="tabs">${RISK_TABS.map(([k,label])=>`<button type="button" class="tab ${tab===k?'active':''}" data-tab="${k}">${label}<em>${counts[k]||0}</em></button>`).join('')}</div>
      <div class="execution-list">${inTab.map(x=>`<button class="execution-row ${x.id===cur?.id?'selected':''}" data-risk="${x.id}"><strong>${esc(x.estimate_code||('结算'+x.review_id))} / ${esc(x.mould_code)}</strong><span>${badge(x)}</span><small>不接受概率 ${x.reject_probability==null?'—':number(x.reject_probability)} / 阈值 ${number(x.threshold)} / 第${x.attempts}次</small></button>`).join('')||empty('当前分栏暂无复核记录。')}</div>
    </section>
    <section class="section">
      ${cur?`<h2>${esc(cur.estimate_code||('结算'+cur.review_id))} · ${badge(cur)}</h2>
        <div class="ribbon">
          <div>模具<strong>${esc(cur.mould_code)}</strong></div>
          <div>发起人<strong>${esc(cur.initiated_by)}</strong></div>
          <div>重试次数<strong class="mono">${cur.attempts}</strong></div>
          <div>状态<strong>${RISK_REVIEW_STATES[cur.state]}</strong></div>
        </div>
        ${basisBlock(cur)}
        ${cur.state==='voided'?`<div class="rule">许可作废：${esc(cur.voided_reason||'同模具登记新整改')}<br>${esc(cur.voided_at||'')}。请重新发起复核。</div>`:''}
        ${cur.state==='blocked'?`<form id="risk-release" data-id="${cur.id}"><label>主管放行说明（高风险，须说明为何仍可发样）<textarea name="release_note" minlength="8" required placeholder="说明客户让步接收、已附整改证据等放行依据"></textarea></label><button class="primary" ${s.user.role!=='admin'?'disabled':''}>主管说明后放行</button></form>`:''}
        ${cur.state==='released'?`<p class="rule">已放行 · ${esc(cur.released_by||'')}<br>${esc(cur.release_note||'')}<br>${esc(cur.released_at||'')}。可在「客户交付验收」页发样；发样后本许可转为已发样。</p>`:''}
        ${cur.state==='dispatched'?`<p class="rule">已发样 · ${esc(cur.dispatched_at||'')}。已交付历史不回写，许可随交付归档。</p>`:''}
        ${['pending','insufficient','blocked'].includes(cur.state)?`<div class="signline"><button class="primary" id="risk-retry" data-id="${cur.id}" ${s.user.role==='viewer'?'disabled':''}>重试复核（不新增记录）</button><p>${cur.state==='pending'?'上次训练中断，重试复用本记录。':cur.state==='insufficient'?'样本积累后重试，仍写回本记录。':'阈值或数据变化后重试，仍写回本记录。'}</p></div>`:''}
      `:empty('先在左侧选择已签结实绩发起验收风险复核。')}
      <div class="threshold-box">
        <h3>主管拦截阈值</h3>
        <form id="risk-threshold">
          <label>不接受概率阈值（0–1，达到即拦截）<input name="threshold" type="number" step="0.01" min="0.01" max="0.99" value="${s.riskThreshold?.threshold??0.5}" required></label>
          <button ${s.user.role!=='admin'?'disabled':''}>保存阈值</button>
          <p class="rule">当前阈值 ${number(s.riskThreshold?.threshold??0.5)}，由 ${esc(s.riskThreshold?.updated_by||'默认')} 于 ${esc(s.riskThreshold?.updated_at||'—')} 设定。仅影响之后发起的复核。</p>
        </form>
      </div>
    </section>
  </div>`;
}

export function bind(s,draw){
  const act=async(fn,msg)=>{try{await fn();await draw();notify(msg)}catch(x){notify(x.message)}};
  document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{s.riskTab=b.dataset.tab;s.riskSelected=null;draw()});
  document.querySelectorAll('[data-risk]').forEach(b=>b.onclick=()=>{s.riskSelected=+b.dataset.risk;draw()});
  const f=document.querySelector('#risk-create');
  if(f)f.onsubmit=e=>{e.preventDefault();const d=Object.fromEntries(new FormData(e.target));if(!d.review_id)return;act(async()=>{const x=await send('/acceptance-risk-reviews',{review_id:+d.review_id});s.riskSelected=x.id},'验收风险复核已完成')};
  const rt=document.querySelector('#risk-retry');
  if(rt)rt.onclick=()=>act(async()=>{const old=s.riskReviews.find(x=>x.id===+rt.dataset.id);const x=await send('/acceptance-risk-reviews',{review_id:old.review_id});s.riskSelected=x.id},'已重试，未新增复核记录');
  const rl=document.querySelector('#risk-release');
  if(rl)rl.onsubmit=e=>{e.preventDefault();const d=Object.fromEntries(new FormData(e.target));act(()=>send('/acceptance-risk-reviews/'+rl.dataset.id+'/release',d),'高风险已由主管说明放行')};
  const tf=document.querySelector('#risk-threshold');
  if(tf)tf.onsubmit=e=>{e.preventDefault();const d=Object.fromEntries(new FormData(e.target));act(()=>send('/risk-threshold',{threshold:+d.threshold},'PUT'),'拦截阈值已更新')};
}
