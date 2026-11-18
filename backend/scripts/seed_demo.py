import random
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func
from src.db import Base,engine,Session
from src.models import User,Mould,MachineSlot,Estimate,TrialHistory
from src.execution_models import TrialExecution,CostReview
from src.workshop_models import MaintenanceWindow,ResinLot,TrialMaterial,ReviewMaterialLedger,TrialDelivery
from src.risk_models import AcceptanceRiskReview, RiskThresholdSetting
from src.auth import hash_password

UTC=timezone.utc
STEEL_CODE={'P20':0,'H13':1,'S136':2}


def _snapshot_for(m, e, x, r, idx):
    """构造与线上发样一致的交付快照，含风险分类器所需固定特征与首次意见标签。"""
    return {
        'review_id': r.id, 'execution_id': x.id, 'estimate_code': e.code,
        'mould_id': m.id, 'mould_code': m.code,
        'cavities': m.cavities, 'slides': m.slides, 'steel': m.steel, 'weight_kg': m.weight_kg,
        'good_shots': x.good_shots, 'total_shots': x.total_shots, 'actual_hours': x.actual_hours,
        'material_cost': r.material_cost, 'variance_pct': r.variance_pct, 'margin': r.margin,
        'signed_by': r.signed_by, 'ended_at': x.ended_at,
        'dispatched_at': (datetime.fromisoformat(x.ended_at)+timedelta(hours=2)).isoformat(),
        'review': {'variance_pct': r.variance_pct, 'margin': r.margin},
        'seed_historical': True, 'seed_index': idx,
    }


def seed_risk_history(s, moulds):
    """为每个模具补足已签收并关闭的历史交付（固定 seed 的业务模拟数据）。

    只写缺失记录（按 tracking 自然键），保留用户后续修改；标签为模拟的首次客户意见。
    整改概率随复杂度、良率下降和工时偏差上升，保证分类器可学到稳定统计关系。
    """
    rng = random.Random(20261118)
    base = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    per_mould = 9  # 3 模具共 27 条 > 全局 20、单模 9 > 单模 6 的样本门槛
    tracking_seen = {d.tracking for d in s.scalars(select(TrialDelivery))}
    made = 0
    for m in moulds:
        for i in range(per_mould):
            tracking = f'RM-HIST-{m.code}-{i+1:02d}'
            if tracking in tracking_seen:
                continue
            started = base + timedelta(days=made * 2)
            hours = round(6 + m.cavities * 0.25 + m.slides * 0.4 + rng.uniform(-0.8, 1.6), 2)
            total = rng.choice([80, 100, 120])
            good = max(int(total - rng.randint(2, 18)), int(total * 0.8))
            rate = 260.0
            material = round(rng.uniform(200, 900), 2)
            actual_cost = round(hours * rate + material, 2)
            quoted = round(actual_cost + rng.uniform(-600, 2200), 2)
            predicted = round(hours * rng.uniform(0.82, 1.15), 2)
            variance_pct = round((hours - predicted) / predicted * 100, 3)
            e = Estimate(code=f'QE-HIST-{m.code}-{i+1:02d}', mould_id=m.id, rate=rate,
                         setup_hours=1, quote_amount=quoted, status='committed', predicted_hours=predicted,
                         model_result={'note': '风险训练用历史模拟估单'})
            s.add(e); s.flush()
            x = TrialExecution(estimate_id=e.id, started_at=started.isoformat(),
                               ended_at=(started + timedelta(hours=hours)).isoformat(),
                               actual_hours=hours, good_shots=good, total_shots=total,
                               status='settled',
                               snapshot={'estimate_code': e.code, 'predicted_hours': predicted,
                                         'quoted_amount': quoted, 'rate': rate,
                                         'mould_code': m.code, 'slot_id': None})
            s.add(x); s.flush()
            r = CostReview(execution_id=x.id, material_cost=material, actual_cost=actual_cost,
                           quoted_amount=quoted,
                           variance_hours=round(hours - predicted, 4), variance_pct=variance_pct,
                           margin=round(quoted - actual_cost, 2), reason='历史训练模拟结算',
                           status='signed', signed_by='admin@ruimo.demo')
            s.add(r); s.flush()
            good_rate = good / total
            z = (-0.15 + 0.035 * m.cavities + 0.08 * m.slides + 0.0007 * m.weight_kg
                 + STEEL_CODE[m.steel] * 0.18 - 2.0 * good_rate + 0.008 * variance_pct
                 + rng.gauss(0, 0.85))
            rectify = 1 / (1 + 2.718281828 ** (-z)) >= 0.5
            decision = 'rectification' if rectify else 'accepted'
            snap = _snapshot_for(m, e, x, r, made + 1)
            snap['first_decision'] = decision
            snap['first_note'] = '历史模拟首次客户意见：' + ('尺寸/装配需整改重试' if rectify else '样件尺寸外观符合要求，予以接受')
            snap['closed_decision'] = decision
            snap['closed_at'] = (started + timedelta(days=3)).isoformat()
            d = TrialDelivery(review_id=r.id, good_samples=min(10, good), tracking=tracking,
                              state='closed', snapshot=snap,
                              received_by='华远质量部-历史', customer_note=snap['first_note'],
                              closed_by='admin@ruimo.demo')
            s.add(d)
            made += 1
    return made


def seed():
    Base.metadata.create_all(engine)
    with Session() as s:
        for role in ('admin','estimator','viewer'):
            if not s.scalar(select(User).where(User.email==role+'@ruimo.demo')): s.add(User(email=role+'@ruimo.demo',role=role,password_hash=hash_password('Mould2026!')))
        for code,name,cav,slides,steel,kg in [('MD-86','八腔医疗接头模',8,4,'S136',860),('MD-42','双腔仪表外壳模',2,2,'P20',420),('MD-115','十六腔盖件模',16,6,'H13',1150)]:
            if not s.scalar(select(Mould).where(Mould.code==code)): s.add(Mould(code=code,name=name,cavities=cav,slides=slides,steel=steel,weight_kg=kg))
        s.flush()
        for machine,start,cap in [('M280','2026-11-18 08:00',48),('M280','2026-11-19 08:00',4),('M450','2026-11-18 08:00',72)]:
            if not s.scalar(select(MachineSlot).where(MachineSlot.machine==machine,MachineSlot.start_hour==start)): s.add(MachineSlot(machine=machine,start_hour=start,capacity_hours=cap,reserved_hours=0))
        rng=random.Random(42)
        for i in range(160):
            code=f'TR-{i+1:03d}'
            if s.scalar(select(TrialHistory).where(TrialHistory.trial_code==code)):continue
            cav=rng.choice([2,4,8,16]);sl=rng.randint(0,8);kg=rng.uniform(180,1600);steel=rng.choice(['P20','H13','S136'])
            hours=2+cav*.3+sl*.65+kg*.004+cav*sl*.035+(['P20','H13','S136'].index(steel))*.8+rng.gauss(0,.4)
            s.add(TrialHistory(trial_code=code,cavities=cav,slides=sl,steel=steel,weight_kg=round(kg,1),actual_hours=round(hours,2)))
        s.flush()
        if not s.scalar(select(Estimate).where(Estimate.code=='QE-26017')):
            m=s.scalar(select(Mould).where(Mould.code=='MD-86'))
            s.add(Estimate(code='QE-26017',mould_id=m.id,rate=280,setup_hours=2,quote_amount=8000,status='draft'))
        s.flush()
        if not s.scalar(select(Estimate).where(Estimate.code=='QE-FLOOR-DEMO')):
            m=s.scalar(select(Mould).where(Mould.code=='MD-42'));slot=s.scalar(select(MachineSlot).where(MachineSlot.machine=='M450'))
            s.add(Estimate(code='QE-FLOOR-DEMO',mould_id=m.id,slot_id=slot.id,rate=300,setup_hours=1,predicted_hours=10,quote_amount=5000,status='committed',model_result={'note':'已承诺的演示订单，供实绩采集；不作模型效果指标'}));slot.reserved_hours+=10
        for code,grade,qty,price,state in [('RS-P20-DEMO','ABS自然色',500,20,'available'),('RS-ISOLATED','ABS复检隔离',50,18,'quarantine')]:
            if not s.scalar(select(ResinLot).where(ResinLot.code==code)):
                s.add(ResinLot(code=code,grade=grade,received_kg=qty,available_kg=qty,unit_price=price,state=state))
        # 风险分类器训练历史：已签收并关闭、带首次客户意见的同模具交付
        moulds=list(s.scalars(select(Mould).order_by(Mould.id)))
        seed_risk_history(s,moulds)
        if not s.scalar(select(RiskThresholdSetting).where(RiskThresholdSetting.id==1)):
            s.add(RiskThresholdSetting(id=1,threshold=0.5))
        s.commit()
        print({t.__tablename__:s.scalar(select(func.count()).select_from(t)) for t in (User,Mould,MachineSlot,Estimate,TrialHistory,TrialExecution,CostReview,MaintenanceWindow,ResinLot,TrialMaterial,ReviewMaterialLedger,TrialDelivery,AcceptanceRiskReview,RiskThresholdSetting)})
if __name__=='__main__':seed()
