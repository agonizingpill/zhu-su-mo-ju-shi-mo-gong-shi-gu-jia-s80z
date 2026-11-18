"""验收风险分类器：用已签收并已关闭的首次客户意见训练，预测再次发样被整改（不接受）的概率。

与 ml.py 的工时估价 Ridge 回归完全独立：不同数据源、不同标签、不同模型，互不影响。
- 训练样本：state='closed' 的 TrialDelivery，标签取关闭时的首次客户意见（rectification=1 / accepted=0）。
- 特征固定取自复核当时的实绩/结算/模具快照，后补意见不会改写既有复核的 basis。
- 按模具分组验证（LeaveOneGroupOut），报告同模具分组留出指标，避免同模具多次交付泄漏。
- 样本不足（全局或该模具）直接拒绝出概率，由上层置为 insufficient 拦截。
"""
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.metrics import accuracy_score, precision_score, recall_score, roc_auc_score
from sqlalchemy import select
from .workshop_models import TrialDelivery
from .constants import RISK_LABELS, RISK_MIN_TOTAL, RISK_MIN_PER_MOULD

FEATURE_NAMES = ['cavities', 'slides', 'steel_code', 'weight_kg', 'good_shots',
                 'total_shots', 'good_rate', 'actual_hours', 'variance_pct', 'margin']
STEEL_CODE = {'P20': 0, 'H13': 1, 'S136': 2}


def feature_from_basis(snapshot, review):
    """从交付快照与结算值构造固定特征向量。缺失值用 0 兜底，保证复核可复现。"""
    total = float(snapshot.get('total_shots') or 0)
    good = float(snapshot.get('good_shots') or 0)
    return [
        float(snapshot.get('cavities') or 0),
        float(snapshot.get('slides') or 0),
        float(STEEL_CODE.get(snapshot.get('steel'), 0)),
        float(snapshot.get('weight_kg') or 0),
        good,
        total,
        round(good / total, 4) if total > 0 else 0.0,
        float(snapshot.get('actual_hours') or 0),
        float(review.get('variance_pct') or 0),
        float(review.get('margin') or 0),
    ]


def collect_training(s):
    """只取已关闭交付的首次客户意见；返回 (X, y, groups, rows)。

    整改关闭保留首次意见 'rectification'，接受关闭为 'accepted'。
    未关闭、未签收的在途交付不进入训练，避免后补/未定意见混入。
    """
    X, y, groups, rows = [], [], [], []
    for d in s.scalars(select(TrialDelivery).where(TrialDelivery.state == 'closed').order_by(TrialDelivery.id)):
        decision = d.snapshot.get('first_decision')
        if decision not in RISK_LABELS:
            continue
        review = d.snapshot.get('review') or {}
        X.append(feature_from_basis(d.snapshot, review))
        y.append(RISK_LABELS[decision])
        groups.append(d.snapshot.get('mould_id'))
        rows.append({'delivery_id': d.id, 'mould_id': d.snapshot.get('mould_id'),
                     'mould_code': d.snapshot.get('mould_code'), 'decision': decision})
    return np.array(X, dtype=float), np.array(y, dtype=int), np.array(groups), rows


def group_validation(model_factory, X, y, groups):
    """按模具分组留出验证：每次留出一个模具的全部交付，其余模具训练后评估该模具。"""
    preds, probs, truths = [], [], []
    splits = 0
    for tr, te in LeaveOneGroupOut().split(X, y, groups):
        # 训练折内两类标签都存在时 LogisticRegression 才能拟合
        if len(set(y[tr])) < 2:
            continue
        m = model_factory()
        m.fit(X[tr], y[tr])
        p = m.predict_proba(X[te])[:, 1]
        probs.extend(p.tolist())
        preds.extend((p >= 0.5).astype(int).tolist())
        truths.extend(y[te].tolist())
        splits += 1
    if not truths:
        return {'splits': 0}
    out = {'splits': splits, 'samples': len(truths),
           'accuracy': round(accuracy_score(truths, preds), 4),
           'precision': round(precision_score(truths, preds, zero_division=0), 4),
           'recall': round(recall_score(truths, preds, zero_division=0), 4)}
    if len(set(truths)) > 1:
        out['auc'] = round(roc_auc_score(truths, probs), 4)
    return out


def build_model():
    # 类不平衡下用 balanced 类权重；标准化保证量纲不同的特征可共同拟合
    return make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000, class_weight='balanced'))


def evaluate(s, target_mould_id, target_features, threshold):
    """训练并对目标模具的待发样实绩给出不接受概率，返回复核依据快照。

    样本不足（全局 < RISK_MIN_TOTAL 或目标模具 < RISK_MIN_PER_MOULD）时返回
    sufficient=False，由上层拦截且不给出概率。
    """
    X, y, groups, rows = collect_training(s)
    per_mould = {}
    for gid in groups:
        per_mould[int(gid)] = per_mould.get(int(gid), 0) + 1
    total = len(y)
    mould_count = int(per_mould.get(int(target_mould_id), 0))
    basis = {
        'training_total': total,
        'min_total_required': RISK_MIN_TOTAL,
        'mould_samples': mould_count,
        'min_mould_required': RISK_MIN_PER_MOULD,
        'mould_group_counts': per_mould,
        'positive_rectification': int(sum(y.tolist())) if total else 0,
        'feature_names': FEATURE_NAMES,
        'features': target_features,
        'threshold': threshold,
        'training_rows': rows,
    }
    if total < RISK_MIN_TOTAL or mould_count < RISK_MIN_PER_MOULD or len(set(y.tolist())) < 2:
        basis['sufficient'] = False
        basis['reason'] = (f'样本不足：全局已关闭意见 {total}/{RISK_MIN_TOTAL}，'
                           f'同模具 {mould_count}/{RISK_MIN_PER_MOULD}，且需同时含接受与整改两类')
        return None, basis
    basis['sufficient'] = True
    basis['group_validation'] = group_validation(build_model, X, y, groups)
    model = build_model()
    model.fit(X, y)
    prob = float(model.predict_proba(np.array([target_features], dtype=float))[0, 1])
    coef = model[-1].coef_[0]
    basis['coefficients'] = [{'name': n, 'coefficient': round(float(c), 4)} for n, c in zip(FEATURE_NAMES, coef)]
    basis['intercept'] = round(float(model[-1].intercept_[0]), 4)
    basis['reject_probability'] = round(prob, 4)
    basis['verdict'] = 'blocked' if prob >= threshold else 'passed'
    return round(prob, 4), basis
