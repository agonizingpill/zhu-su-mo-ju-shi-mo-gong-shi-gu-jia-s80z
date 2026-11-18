"""纯标准库验证分类器数学口径与种子标签充足性（无 numpy/sklearn 环境）。

逻辑与 backend/src/risk_ml.py 等价：标准化 + L2 逻辑回归 + balanced 类权重，
按模具 LeaveOneGroupOut 分组验证；并复算 seed_demo 的 sigmoid 标签生成，
确认全局/单模样本门限可被满足、两类标签都存在。
启动：python3 -I classifier_math_test.py
"""
import math
import random
import sys

PASS, FAIL = 0, 0


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print('  PASS ' + name)
    else:
        FAIL += 1
        print('  FAIL ' + name + ' ' + detail)


def sigmoid(z):
    return 1.0 / (1.0 + math.exp(-max(-60, min(60, z))))


def standardize_fit(X):
    n = len(X)
    d = len(X[0])
    mu = [sum(row[j] for row in X) / n for j in range(d)]
    var = [sum((row[j] - mu[j]) ** 2 for row in X) / n for j in range(d)]
    sd = [math.sqrt(v) if v > 1e-12 else 1.0 for v in var]
    return mu, sd


def apply_scale(X, mu, sd):
    return [[(row[j] - mu[j]) / sd[j] for j in range(len(mu))] for row in X]


def train_logistic(X, y, weights, lr=0.3, epochs=4000, l2=1.0):
    """带 L2 正则与样本权重的逻辑回归（梯度下降），对应 sklearn LogisticRegression。"""
    d = len(X[0])
    w = [0.0] * d
    b = 0.0
    n = len(y)
    for _ in range(epochs):
        gw = [0.0] * d
        gb = 0.0
        for i in range(n):
            p = sigmoid(sum(w[j] * X[i][j] for j in range(d)) + b)
            err = (p - y[i]) * weights[i]
            for j in range(d):
                gw[j] += err * X[i][j]
            gb += err
        for j in range(d):
            gw[j] = gw[j] / n + l2 * w[j] / n
            w[j] -= lr * gw[j]
        b -= lr * gb / n
    return w, b


def proba(w, b, x):
    return sigmoid(sum(w[j] * x[j] for j in range(len(w))) + b)


# ---- 1. 可分数据上概率单调、阈值门限生效 ----
print('== A. 逻辑回归概率与阈值拦截口径 ==')
random.seed(7)
# 高复杂度+低良率 -> 整改(1)；低复杂度+高良率 -> 接受(0)
X, y, groups = [], [], []
for g in range(4):
    for i in range(12):
        complex_ = i >= 6
        cav = (16 if complex_ else 2) + random.randint(-1, 1)
        good_rate = (0.82 if complex_ else 0.97) + random.uniform(-0.02, 0.02)
        var_pct = (18 if complex_ else -6) + random.uniform(-3, 3)
        X.append([cav, round(good_rate, 3), var_pct])
        y.append(1 if complex_ else 0)
        groups.append(g)
mu, sd = standardize_fit(X)
Xs = apply_scale(X, mu, sd)
n1, n0 = sum(y), len(y) - sum(y)
wclass = {0: len(y) / (2 * n0), 1: len(y) / (2 * n1)}  # balanced
wts = [wclass[t] for t in y]
w, b = train_logistic(Xs, y, wts)
probs = [proba(w, b, row) for row in Xs]
high = [p for p, t in zip(probs, y) if t == 1]
low = [p for p, t in zip(probs, y) if t == 0]
check('整改样本概率整体高于接受样本', sum(high) / len(high) > sum(low) / len(low),
      f'{sum(high)/len(high):.3f} vs {sum(low)/len(low):.3f}')
check('所有概率落在 (0,1)', all(0 < p < 1 for p in probs))
check('阈值 0.5 门限：高概率被判拦截', all(p >= 0.5 for p in high[:3]))
acc = sum(1 for p, t in zip(probs, y) if (1 if p >= 0.5 else 0) == t) / len(y)
check('可分数据准确率高', acc > 0.8, f'acc={acc:.3f}')

# ---- 2. 按模具 LeaveOneGroupOut：留出折不泄漏同组 ----
print('== B. 按模具分组留出验证 ==')
logo_correct, logo_total, folds = 0, 0, 0
for g in set(groups):
    tr = [i for i in range(len(y)) if groups[i] != g]
    te = [i for i in range(len(y)) if groups[i] == g]
    if len({y[i] for i in tr}) < 2:
        continue
    Xtr = apply_scale([X[i] for i in tr], *standardize_fit([X[i] for i in tr]))
    ytr = [y[i] for i in tr]
    n1t, n0t = sum(ytr), len(ytr) - sum(ytr)
    wct = {0: len(ytr) / (2 * n0t), 1: len(ytr) / (2 * n1t)}
    wt, bt = train_logistic(Xtr, ytr, [wct[t] for t in ytr])
    mu_t, sd_t = standardize_fit([X[i] for i in tr])
    for i in te:
        xt = apply_scale([X[i]], mu_t, sd_t)[0]
        pred = 1 if proba(wt, bt, xt) >= 0.5 else 0
        logo_correct += int(pred == y[i])
        logo_total += 1
    folds += 1
check('LOGO 折数等于模具组数', folds == len(set(groups)), f'folds={folds}')
check('LOGO 评估覆盖全部样本', logo_total == len(y))
check('LOGO 留出准确率 > 0.75', logo_correct / logo_total > 0.75,
      f'{logo_correct}/{logo_total}')

# ---- 3. 样本不足门限：与 constants.RISK_MIN_* 一致 ----
print('== C. 样本不足拦截（不出概率）==')
RISK_MIN_TOTAL, RISK_MIN_PER_MOULD = 20, 6
for total, mould_n, expect_block in [(10, 9, True), (27, 3, True), (27, 9, False)]:
    blocked = total < RISK_MIN_TOTAL or mould_n < RISK_MIN_PER_MOULD
    check(f'总{total}/单模{mould_n} -> {"拦截" if expect_block else "可评估"}',
          blocked == expect_block)

# ---- 4. 复算 seed_demo 标签生成，确认两类标签与门限 ----
print('== D. 种子历史标签充足性（复刻 seed_demo 公式）==')
STEEL_CODE = {'P20': 0, 'H13': 1, 'S136': 2}
rng = random.Random(20261118)
moulds = [('MD-86', 8, 4, 'S136', 860), ('MD-42', 2, 2, 'P20', 420),
          ('MD-115', 16, 6, 'H13', 1150)]
per_mould = 9
all_labels, per = [], {}
for code, cav, sl, steel, kg in moulds:
    labels = []
    for i in range(per_mould):
        total = rng.choice([80, 100, 120])
        good = max(int(total - rng.randint(2, 18)), int(total * 0.8))
        # hours/预测/偏差仅影响 z，按种子同序调用 RNG 保持一致
        hours = round(6 + cav * 0.25 + sl * 0.4 + rng.uniform(-0.8, 1.6), 2)
        predicted = round(hours * rng.uniform(0.82, 1.15), 2)
        variance_pct = round((hours - predicted) / predicted * 100, 3)
        good_rate = good / total
        z = (-0.15 + 0.035 * cav + 0.08 * sl + 0.0007 * kg
             + STEEL_CODE[steel] * 0.18 - 2.0 * good_rate + 0.008 * variance_pct
             + rng.gauss(0, 0.85))
        labels.append(1 if sigmoid(z) >= 0.5 else 0)
    per[code] = labels
    all_labels += labels
check('全局训练样本 27 >= 20', len(all_labels) >= RISK_MIN_TOTAL)
check('每个模具 9 条 >= 6', all(len(v) >= RISK_MIN_PER_MOULD for v in per.values()))
check('整改与接受两类标签都存在', 0 < sum(all_labels) < len(all_labels),
      f'整改={sum(all_labels)} 接受={len(all_labels)-sum(all_labels)}')
check('每个模具内部两类都存在（分组验证可用）',
      all(0 < sum(v) < len(v) for v in per.values()), str({k: sum(v) for k, v in per.items()}))
print('   各模具整改条数：', {k: sum(v) for k, v in per.items()})

print(f'\n结果：{PASS} 通过 / {FAIL} 失败')
sys.exit(1 if FAIL else 0)
