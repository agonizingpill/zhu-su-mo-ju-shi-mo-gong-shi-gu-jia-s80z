"""验收风险复核服务：状态机、并发拦截、旧许可作废与发样联动。

关键约束：
- 复核固定复核当时实绩与已关闭的首次客户意见，后补意见不混入（basis 快照固化）。
- 复核中断/失败后重试复用同一条记录，不新增复核。
- 同模具出现新整改（关闭交付）时，该模具既往放行/通过许可一律作废。
- 发样与整改登记在同一事务里以相同加锁顺序互斥，并发请求一并拦住。
- 已交付历史不回写：本功能上线前的交付不补风险复核。
"""
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy import select
from .models import Mould, Estimate
from .execution_models import TrialExecution, CostReview
from .workshop_models import TrialDelivery
from .risk_models import AcceptanceRiskReview, RiskThresholdSetting
from .risk_ml import evaluate, feature_from_basis
from .constants import RISK_DEFAULT_THRESHOLD
from .services import item, serialize, record


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def get_threshold(s):
    setting = s.get(RiskThresholdSetting, 1)
    if not setting:
        setting = RiskThresholdSetting(id=1, threshold=RISK_DEFAULT_THRESHOLD)
        s.add(setting)
        s.flush()
    return setting


def target_features(s, review):
    """从待发样实绩/结算及其模具构造分类器特征（复核当时取值，随后固化进 basis）。"""
    execution = item(s, TrialExecution, review.execution_id)
    estimate = item(s, Estimate, execution.estimate_id)
    mould = item(s, Mould, estimate.mould_id)
    snapshot = {
        'cavities': mould.cavities,
        'slides': mould.slides,
        'steel': mould.steel,
        'weight_kg': mould.weight_kg,
        'good_shots': execution.good_shots,
        'total_shots': execution.total_shots,
        'actual_hours': execution.actual_hours,
    }
    review_basis = {'variance_pct': review.variance_pct, 'margin': review.margin}
    return feature_from_basis(snapshot, review_basis), mould


def view(s, x):
    d = serialize(x)
    mould = s.get(Mould, x.mould_id)
    d['mould_code'] = mould.code if mould else f'#{x.mould_id}'
    review = s.get(CostReview, x.review_id)
    if review:
        execution = s.get(TrialExecution, review.execution_id)
        d['estimate_code'] = execution.snapshot['estimate_code'] if execution else f'结算{review.id}'
    d['can_dispatch'] = x.state == 'released'
    d['can_release'] = x.state == 'blocked'
    d['can_retry'] = x.state in ('pending', 'insufficient', 'blocked')
    return d


def initiate(s, p, u):
    """发起复核；对同一条复核重试时复用记录、累加 attempts，不新增。

    仅当历史复核处于作废（voided）时才新建一条，旧作废记录保留可追溯；
    其余在途/已放行/已发样复核均复用同一条，杜绝重复复核。
    """
    review = item(s, CostReview, p.review_id)
    if review.status != 'signed':
        raise HTTPException(409, '只有已签结实绩可发起验收风险复核')
    # 只取未作废的有效复核；部分唯一索引保证该查询至多一条
    x = s.scalar(select(AcceptanceRiskReview)
                 .where(AcceptanceRiskReview.review_id == review.id,
                        AcceptanceRiskReview.state != 'voided')
                 .with_for_update().execution_options(populate_existing=True))
    if x:
        if x.state in ('released', 'dispatched'):
            raise HTTPException(409, '该复核已放行或已发样，无须重复复核；被作废后须重新发起')
        x.attempts += 1
        action = '重试验收风险复核 '
    else:
        execution = item(s, TrialExecution, review.execution_id)
        estimate = item(s, Estimate, execution.estimate_id)
        x = AcceptanceRiskReview(review_id=review.id, mould_id=estimate.mould_id,
                                 state='pending', threshold=get_threshold(s).threshold,
                                 initiated_by=u.email, attempts=1, basis={})
        s.add(x)
        s.flush()
        action = '发起验收风险复核 '
    x.state = 'pending'
    s.flush()
    try:
        features, _mould = target_features(s, review)
        prob, basis = evaluate(s, x.mould_id, features, x.threshold)
        x.basis = basis
        x.completed_at = now_iso()
        # 重试可能改变结论，先清掉上一轮放行/发样/作废残留，避免脏字段
        x.released_by = None
        x.released_at = None
        x.release_note = ''
        if not basis.get('sufficient'):
            x.state = 'insufficient'
            x.reject_probability = None
        elif prob is None:
            x.state = 'insufficient'
            x.reject_probability = None
        elif prob >= x.threshold:
            x.state = 'blocked'
            x.reject_probability = prob
        else:
            # 低风险通过即视为有效发样许可，仍记录在案，等待发样
            x.state = 'released'
            x.reject_probability = prob
            x.released_by = u.email
            x.released_at = x.completed_at
            x.release_note = f'不接受概率 {prob:.4f} 低于阈值 {x.threshold:.4f}，自动通过'
    except Exception as exc:  # 训练中断：保留 pending，允许重试且不新增复核
        x.state = 'pending'
        x.basis = {'error': f'复核训练中断：{exc}，可重试且不会新增复核'}
        record(s, u, action + str(x.id) + ' 中断')
        s.commit()
        raise HTTPException(503, '风险复核训练中断，可重试且不会新增复核记录')
    record(s, u, action + str(x.id) + ' 结论 ' + x.state)
    s.commit()
    return view(s, x)


def release(s, rid, p, u):
    """高风险拦截后，主管填写说明放行。"""
    x = s.scalar(select(AcceptanceRiskReview).where(AcceptanceRiskReview.id == rid)
                 .with_for_update().execution_options(populate_existing=True))
    if not x:
        raise HTTPException(404, '风险复核不存在')
    if x.state == 'voided':
        raise HTTPException(409, '许可已因同模新整改作废，放行无效，请重新复核')
    if x.state != 'blocked':
        raise HTTPException(409, '仅风险拦截状态可由主管放行，不能重复放行')
    x.state = 'released'
    x.release_note = p.release_note.strip()
    x.released_by = u.email
    x.released_at = now_iso()
    record(s, u, '主管放行高风险复核 ' + str(x.id))
    s.commit()
    return view(s, x)


def update_threshold(s, p, u):
    setting = get_threshold(s)
    setting.threshold = p.threshold
    setting.updated_by = u.email
    setting.updated_at = now_iso()
    record(s, u, f'设定验收风险拦截阈值 {p.threshold:.4f}')
    s.commit()
    return serialize(setting)


def ensure_dispatch_permit(s, review_id):
    """发样前校验门：必须存在状态为 released 的同结算风险复核。

    在 deliver 事务内、对结算行加锁后调用，与整改登记互斥。
    已交付历史不回写：本门只作用于新发样。
    """
    x = s.scalar(select(AcceptanceRiskReview)
                 .where(AcceptanceRiskReview.review_id == review_id,
                        AcceptanceRiskReview.state != 'voided')
                 .with_for_update().execution_options(populate_existing=True))
    if not x:
        raise HTTPException(409, '该签结尚未通过验收风险复核（或许可已作废），不能发样，请先发起复核')
    if x.state == 'insufficient':
        raise HTTPException(409, '风险复核样本不足已拦截，暂不能发样；待同模意见积累后重试复核')
    if x.state == 'blocked':
        raise HTTPException(409, f'不接受概率 {x.reject_probability:.4f} 达到拦截阈值，须主管说明后放行')
    if x.state == 'pending':
        raise HTTPException(409, '风险复核尚未完成或上次中断，请重试复核后再发样')
    if x.state != 'released':
        raise HTTPException(409, '风险复核未放行，不能发样')
    return x


def mark_dispatched(s, review_id):
    x = s.scalar(select(AcceptanceRiskReview)
                 .where(AcceptanceRiskReview.review_id == review_id,
                        AcceptanceRiskReview.state != 'voided')
                 .with_for_update().execution_options(populate_existing=True))
    if x and x.state == 'released':
        x.state = 'dispatched'
        x.dispatched_at = now_iso()


def invalidate_for_rectification(s, mould_id, u):
    """同模具有新整改关闭时，作废该模具既往放行/通过/已发样前的有效许可。

    已发样（dispatched）代表交付已实际发生，属已交付历史，不回写、不作废；
    仅作废仍处于 released（已放行未发样）的许可，blocked/insufficient/pending 保持原态。
    """
    rows = s.scalars(select(AcceptanceRiskReview)
                     .where(AcceptanceRiskReview.mould_id == mould_id,
                            AcceptanceRiskReview.state == 'released')
                     .with_for_update().execution_options(populate_existing=True))
    count = 0
    reason = '同模具登记新整改，旧放行许可作废，须重新复核'
    for x in rows:
        x.state = 'voided'
        x.voided_reason = reason
        x.voided_at = now_iso()
        count += 1
    if count:
        record(s, u, f'同模新整改作废风险放行 {count} 条 / 模具 {mould_id}')
    return count
