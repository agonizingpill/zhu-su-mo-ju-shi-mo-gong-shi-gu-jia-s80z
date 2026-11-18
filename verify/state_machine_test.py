"""运行真实 risk_service / workshop_service，验证状态机、发样门、作废与重试规则。

仅用标准库；SQLAlchemy/FastAPI 由 stub_orm 内存桩替代，分类器用可控桩替代。
启动方式：python3 -I state_machine_test.py
"""
import os
import sys
import types
from pathlib import Path

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent / 'backend'
sys.path.insert(0, str(HERE))

import stub_orm  # noqa: E402
stub_orm.install(BACKEND)
import sqlalchemy  # noqa: E402

# 可控分类器桩：默认行为可被单个测试覆盖
fake_ml = types.ModuleType('src.risk_ml')
fake_ml.FEATURE_NAMES = ['cavities', 'slides', 'steel_code', 'weight_kg', 'good_shots',
                         'total_shots', 'good_rate', 'actual_hours', 'variance_pct', 'margin']


def _features(snapshot, review):
    total = float(snapshot.get('total_shots') or 0)
    good = float(snapshot.get('good_shots') or 0)
    return [float(snapshot.get('cavities') or 0), float(snapshot.get('slides') or 0), 1.0,
            float(snapshot.get('weight_kg') or 0), good, total,
            round(good / total, 4) if total else 0.0,
            float(snapshot.get('actual_hours') or 0),
            float(review.get('variance_pct') or 0), float(review.get('margin') or 0)]


SCRIPT = {'mode': 'low'}


def _evaluate(s, mould_id, features, threshold):
    if SCRIPT['mode'] == 'error':
        raise RuntimeError('boom')
    if SCRIPT['mode'] == 'insufficient':
        return None, {'sufficient': False, 'reason': '样本不足', 'training_total': 3,
                      'min_total_required': 20, 'mould_samples': 1, 'min_mould_required': 6}
    prob = {'low': 0.12, 'high': 0.83}[SCRIPT['mode']]
    return prob, {'sufficient': True, 'reject_probability': prob,
                  'verdict': 'blocked' if prob >= threshold else 'passed', 'threshold': threshold,
                  'training_total': 27, 'mould_samples': 9, 'positive_rectification': 11,
                  'feature_names': fake_ml.FEATURE_NAMES, 'features': features,
                  'group_validation': {'splits': 3, 'samples': 27, 'accuracy': 0.81}}


fake_ml.feature_from_basis = _features
fake_ml.evaluate = _evaluate
sys.modules['src.risk_ml'] = fake_ml

from src.db import Session  # noqa: E402
from src.models import Mould, Estimate  # noqa: E402
from src.execution_models import TrialExecution, CostReview  # noqa: E402
from src.workshop_models import TrialDelivery  # noqa: E402
from src.risk_models import AcceptanceRiskReview  # noqa: E402
from src import risk_service as rs  # noqa: E402
from src import workshop_service as ws  # noqa: E402

PASS, FAIL = 0, 0
EST = types.SimpleNamespace(email='estimator@ruimo.demo', role='estimator')
ADM = types.SimpleNamespace(email='admin@ruimo.demo', role='admin')
_SEQ = 0


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  PASS {name}')
    else:
        FAIL += 1
        print(f'  FAIL {name} {detail}')


def expect_conflict(name, fn, code=409):
    try:
        fn()
        check(name, False, '未按预期拦截')
    except Exception as ex:
        check(name, getattr(ex, 'status_code', None) == code, str(ex))


def make_chain(s, mould, *, signed=True, started='2026-10-01T00:00:00+00:00',
               ended='2026-10-01T09:00:00+00:00'):
    global _SEQ
    _SEQ += 1
    e = Estimate(code=f'QE-T{mould.id}-{_SEQ}', mould_id=mould.id, rate=300, setup_hours=1,
                 quote_amount=5000, status='committed', predicted_hours=10, slot_id=1)
    s.add(e)
    x = TrialExecution(estimate_id=e.id, started_at=started, ended_at=ended,
                       actual_hours=9.0, good_shots=94, total_shots=100, status='settled',
                       snapshot={'estimate_code': e.code, 'mould_code': mould.code,
                                 'predicted_hours': 10, 'quoted_amount': 5000, 'rate': 300})
    s.add(x)
    r = CostReview(execution_id=x.id, material_cost=500, actual_cost=3200,
                   quoted_amount=5000, variance_hours=-1, variance_pct=-10, margin=1800,
                   reason='ok', status='signed' if signed else 'draft', signed_by='admin')
    s.add(r)
    return e, x, r


def delivery_input(review, tracking):
    obj = types.SimpleNamespace(review_id=review.id, good_samples=10, tracking=tracking)
    obj.model_dump = lambda: {'review_id': obj.review_id, 'good_samples': obj.good_samples,
                              'tracking': obj.tracking}
    return obj


def risk_row(s, review_id):
    return s.scalar(sqlalchemy.select(AcceptanceRiskReview)
                    .where(AcceptanceRiskReview.review_id == review_id,
                           AcceptanceRiskReview.state != 'voided'))


def reset():
    Session._store.clear()
    SCRIPT['mode'] = 'low'


print('== 1. 样本不足：拦截发样，不出概率 ==')
reset()
with Session() as s:
    m = Mould(code='MD-1', name='t', cavities=8, slides=4, steel='S136', weight_kg=860)
    s.add(m)
    _, _, r = make_chain(s, m)
    SCRIPT['mode'] = 'insufficient'
    out = rs.initiate(s, types.SimpleNamespace(review_id=r.id), EST)
    check('复核状态 insufficient', out['state'] == 'insufficient')
    check('无概率', out['reject_probability'] is None)
    expect_conflict('无放行业务发样被拦截',
                    lambda: ws.deliver(s, delivery_input(r, 'TRK-1'), EST))

print('== 2. 低风险自动放行 -> 发样 -> 复核转已发样 ==')
reset()
with Session() as s:
    m = Mould(code='MD-1', name='t', cavities=8, slides=4, steel='S136', weight_kg=860)
    s.add(m)
    _, _, r = make_chain(s, m)
    out = rs.initiate(s, types.SimpleNamespace(review_id=r.id), EST)
    check('低风险自动 released', out['state'] == 'released')
    check('概率已记录', out['reject_probability'] == 0.12)
    d = ws.deliver(s, delivery_input(r, 'TRK-2'), EST)
    check('发样成功', d['state'] == 'dispatched')
    check('快照含分类器特征', all(k in d['snapshot'] for k in ('cavities', 'steel', 'good_shots', 'review')))
    rr = risk_row(s, r.id)
    check('复核转为 dispatched', rr.state == 'dispatched' and rr.dispatched_at is not None)

print('== 3. 高风险拦截，主管说明后放行 ==')
reset()
with Session() as s:
    m = Mould(code='MD-1', name='t', cavities=8, slides=4, steel='S136', weight_kg=860)
    s.add(m)
    _, _, r = make_chain(s, m)
    SCRIPT['mode'] = 'high'
    out = rs.initiate(s, types.SimpleNamespace(review_id=r.id), EST)
    check('高风险 blocked', out['state'] == 'blocked' and out['reject_probability'] == 0.83)
    expect_conflict('拦截态发样被拒', lambda: ws.deliver(s, delivery_input(r, 'TRK-3'), EST))
    rel = rs.release(s, out['id'],
                     types.SimpleNamespace(release_note='客户让步接收并附尺寸整改证据，准予放行'), ADM)
    check('主管放行 released', rel['state'] == 'released' and rel['released_by'] == 'admin@ruimo.demo')
    expect_conflict('重复放行被拒',
                    lambda: rs.release(s, rel['id'], types.SimpleNamespace(release_note='再次说明'), ADM))
    d = ws.deliver(s, delivery_input(r, 'TRK-3b'), EST)
    check('放行后可发样', d['state'] == 'dispatched')

print('== 4. 主管设定阈值，仅影响后续复核 ==')
reset()
with Session() as s:
    check('默认阈值 0.5', rs.get_threshold(s).threshold == 0.5)
    rs.update_threshold(s, types.SimpleNamespace(threshold=0.35), ADM)
    m = Mould(code='MD-1', name='t', cavities=8, slides=4, steel='S136', weight_kg=860)
    s.add(m)
    _, _, r = make_chain(s, m)
    SCRIPT['mode'] = 'low'
    out = rs.initiate(s, types.SimpleNamespace(review_id=r.id), EST)
    check('新复核采用新阈值', out['threshold'] == 0.35)

print('== 5. 中断后重试复用同一条，不新增，attempts 累加 ==')
reset()
with Session() as s:
    m = Mould(code='MD-1', name='t', cavities=8, slides=4, steel='S136', weight_kg=860)
    s.add(m)
    _, _, r = make_chain(s, m)
    SCRIPT['mode'] = 'error'
    try:
        rs.initiate(s, types.SimpleNamespace(review_id=r.id), EST)
        check('中断抛 503', False)
    except Exception as ex:
        check('中断抛 503', getattr(ex, 'status_code', None) == 503)
    rows = s.scalars(sqlalchemy.select(AcceptanceRiskReview))
    check('中断仍保留一条 pending', len(rows) == 1 and rows[0].state == 'pending')
    SCRIPT['mode'] = 'low'
    out = rs.initiate(s, types.SimpleNamespace(review_id=r.id), EST)
    rows2 = s.scalars(sqlalchemy.select(AcceptanceRiskReview))
    check('重试未新增记录', len(rows2) == 1)
    check('attempts=2 且成功', rows2[0].attempts == 2 and out['state'] == 'released')

print('== 5b. 重试改变结论时清理上一轮放行残留 ==')
reset()
with Session() as s:
    m = Mould(code='MD-1', name='t', cavities=8, slides=4, steel='S136', weight_kg=860)
    s.add(m)
    _, _, r = make_chain(s, m)
    SCRIPT['mode'] = 'high'
    first = rs.initiate(s, types.SimpleNamespace(review_id=r.id), EST)
    check('首轮 blocked', first['state'] == 'blocked')
    SCRIPT['mode'] = 'low'
    again = rs.initiate(s, types.SimpleNamespace(review_id=r.id), EST)
    check('重试转 released', again['state'] == 'released')
    check('自动通过说明已写入', again['release_note'].startswith('不接受概率'))
    check('attempts 累加到 2', again['attempts'] == 2)

print('== 6. 同模新整改作废旧放行；已发样历史不回写（走真实 close_delivery）==')
reset()
with Session() as s:
    m = Mould(code='MD-2', name='t', cavities=2, slides=2, steel='P20', weight_kg=420)
    s.add(m)
    # B 先建（较早），A 次之，C 为 B 之后的新同模整改实绩
    _, _, rB = make_chain(s, m, started='2026-09-05T00:00:00+00:00', ended='2026-09-05T08:00:00+00:00')
    _, _, rA = make_chain(s, m, started='2026-09-01T00:00:00+00:00', ended='2026-09-01T08:00:00+00:00')
    _, _, rC = make_chain(s, m, started='2026-09-10T00:00:00+00:00', ended='2026-09-10T08:00:00+00:00')
    rs.initiate(s, types.SimpleNamespace(review_id=rA.id), EST)
    rs.initiate(s, types.SimpleNamespace(review_id=rB.id), EST)
    ws.deliver(s, delivery_input(rB, 'TRK-B'), EST)
    dB = s.scalar(sqlalchemy.select(TrialDelivery).where(TrialDelivery.review_id == rB.id))
    ws.receive_delivery(s, dB.id, types.SimpleNamespace(received_by='客户乙'), EST)
    ws.decide_delivery(s, dB.id, types.SimpleNamespace(
        decision='rectification', customer_note='卡扣装配间隙超差需整改重试'), EST)
    ws.close_delivery(s, dB.id, types.SimpleNamespace(correction_review_id=rC.id), ADM)
    check('整改关闭成功', dB.state == 'closed' and dB.correction_review_id == rC.id)
    check('首次整改意见已冻结', dB.snapshot['first_decision'] == 'rectification')
    rA_risk = s.scalar(sqlalchemy.select(AcceptanceRiskReview)
                       .where(AcceptanceRiskReview.review_id == rA.id,
                              AcceptanceRiskReview.state == 'voided'))
    rB_risk = risk_row(s, rB.id)
    check('A 许可 voided', rA_risk.state == 'voided' and bool(rA_risk.voided_reason))
    check('B 已发样历史不回写', rB_risk.state == 'dispatched')
    expect_conflict('作废后发样被拦截', lambda: ws.deliver(s, delivery_input(rA, 'TRK-A'), EST))
    expect_conflict('作废态放行无效',
                    lambda: rs.release(s, rA_risk.id,
                                       types.SimpleNamespace(release_note='试图放行作废许可'), ADM))

print('== 7. 接受路径关闭后首次意见固化为训练标签 ==')
reset()
with Session() as s:
    m = Mould(code='MD-3', name='t', cavities=16, slides=6, steel='H13', weight_kg=1150)
    s.add(m)
    _, _, r = make_chain(s, m)
    rs.initiate(s, types.SimpleNamespace(review_id=r.id), EST)
    d = ws.deliver(s, delivery_input(r, 'TRK-7'), EST)
    d_obj = s.scalar(sqlalchemy.select(TrialDelivery).where(TrialDelivery.id == d['id']))
    ws.receive_delivery(s, d_obj.id, types.SimpleNamespace(received_by='客户甲'), EST)
    ws.decide_delivery(s, d_obj.id, types.SimpleNamespace(
        decision='accepted', customer_note='尺寸外观符合要求予以接受'), EST)
    check('首次意见已固化', d_obj.snapshot['first_decision'] == 'accepted')
    ws.close_delivery(s, d_obj.id, types.SimpleNamespace(correction_review_id=None), ADM)
    check('关闭后标签冻结', d_obj.state == 'closed' and d_obj.snapshot['closed_decision'] == 'accepted')

print('== 9. 作废后重新发起：新建复核、旧记录保留，重新放行后可发样 ==')
reset()
with Session() as s:
    m = Mould(code='MD-9', name='t', cavities=8, slides=4, steel='S136', weight_kg=860)
    s.add(m)
    _, _, r = make_chain(s, m)
    rs.initiate(s, types.SimpleNamespace(review_id=r.id), EST)
    # 同模新整改作废该许可
    rs.invalidate_for_rectification(s, m.id, ADM)
    old = s.scalar(sqlalchemy.select(AcceptanceRiskReview)
                   .where(AcceptanceRiskReview.review_id == r.id,
                          AcceptanceRiskReview.state == 'voided'))
    check('原许可作废保留', old.state == 'voided')
    # 重新发起：应新建第二条，而不是复用旧记录
    new = rs.initiate(s, types.SimpleNamespace(review_id=r.id), EST)
    all_rows = s.scalars(sqlalchemy.select(AcceptanceRiskReview)
                         .where(AcceptanceRiskReview.review_id == r.id))
    check('作废后新建第二条复核', len(all_rows) == 2)
    check('新复核 attempts 从 1 开始', new['attempts'] == 1 and new['state'] == 'released')
    check('旧作废记录仍在', any(x.state == 'voided' for x in all_rows))
    # 新复核放行后可正常发样
    d = ws.deliver(s, delivery_input(r, 'TRK-9'), EST)
    check('重新复核放行后发样成功', d['state'] == 'dispatched')
    active = risk_row(s, r.id)
    check('有效复核转为已发样', active.state == 'dispatched')

print('== 8. 并发：发样与整改登记互斥（共享库下后到者见到作废许可）==')
reset()
s1, s2 = Session(), Session()
m = Mould(code='MD-5', name='t', cavities=4, slides=3, steel='H13', weight_kg=500)
s1.add(m)
_, _, r = make_chain(s1, m)
rs.initiate(s1, types.SimpleNamespace(review_id=r.id), EST)
rs.invalidate_for_rectification(s2, m.id, ADM)
expect_conflict('并发下作废许可拦截发样', lambda: ws.deliver(s1, delivery_input(r, 'TRK-8'), EST))

print(f'\n结果：{PASS} 通过 / {FAIL} 失败')
sys.exit(1 if FAIL else 0)
