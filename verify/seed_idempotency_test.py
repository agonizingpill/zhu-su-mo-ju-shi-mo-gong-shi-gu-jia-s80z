"""验证 seed_demo 的幂等性：连跑两次，风险历史与各表不重复新增。

numpy/sklearn 不参与 seed 逻辑（仅 ORM 写入），用 stub_orm 即可运行真实 seed 模块。
启动：python3 -I seed_idempotency_test.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import stub_orm  # noqa: E402
stub_orm.install(HERE.parent / 'backend')

from src.db import Session  # noqa: E402
from src.workshop_models import TrialDelivery  # noqa: E402
from src.risk_models import AcceptanceRiskReview, RiskThresholdSetting  # noqa: E402
from src.models import Estimate  # noqa: E402
import sqlalchemy  # noqa: E402

# seed_demo 顶部 import 链不触碰 numpy/sklearn，可直接导入
sys.path.insert(0, str(HERE.parent / 'backend'))
from scripts.seed_demo import seed, seed_risk_history  # noqa: E402
from src.models import Mould  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print('  PASS ' + name)
    else:
        FAIL += 1
        print('  FAIL ' + name + ' ' + detail)


def count(model):
    return len(Session._store.get(model, {}))


print('== 首次 seed ==')
seed()
first_deliveries = count(TrialDelivery)
first_estimates = count(Estimate)
first_settings = count(RiskThresholdSetting)
check('生成 27 条历史交付', first_deliveries == 27, f'got {first_deliveries}')
check('阈值单行', first_settings == 1)
check('历史交付均已关闭', all(d.state == 'closed' for d in Session._store[TrialDelivery].values()))
check('每条都有首次意见标签',
      all(d.snapshot.get('first_decision') in ('accepted', 'rectification')
          for d in Session._store[TrialDelivery].values()))
check('无遗留风险复核（待业务发起）', count(AcceptanceRiskReview) == 0)
per_mould = {}
for d in Session._store[TrialDelivery].values():
    per_mould[d.snapshot['mould_id']] = per_mould.get(d.snapshot['mould_id'], 0) + 1
check('三个模具各 9 条', sorted(per_mould.values()) == [9, 9, 9], str(per_mould))

print('== 第二次 seed（幂等）==')
seed()
check('历史交付不重复', count(TrialDelivery) == first_deliveries)
check('估单不重复', count(Estimate) == first_estimates)
check('阈值仍单行', count(RiskThresholdSetting) == 1)
trackings = [d.tracking for d in Session._store[TrialDelivery].values()]
check('tracking 自然键唯一', len(trackings) == len(set(trackings)))

print(f'\n结果：{PASS} 通过 / {FAIL} 失败')
sys.exit(1 if FAIL else 0)
