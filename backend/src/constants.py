ROLES = ('admin', 'estimator', 'viewer')
ESTIMATE_STATES = ('draft', 'calculating', 'estimated', 'requested', 'committed', 'failed')
STEELS = ('P20', 'H13', 'S136')

EXECUTION_STATES = ('running', 'completed', 'settled')
REVIEW_STATES = ('draft', 'signed')

MAINTENANCE_STATES=('planned','approved','completed')
RESIN_STATES=('available','quarantine')
MATERIAL_STATES=('issued','returned')
DELIVERY_STATES=('dispatched','received','accepted','rectification','closed')

# 验收风险复核：待复核（含失败可重试）、样本不足拦截、风险拦截（主管可放行）、已放行、已发样、旧许可作废
RISK_REVIEW_STATES=('pending','insufficient','blocked','released','dispatched','voided')
# 分类器训练样本只取已签收并已关闭的首次客户意见；接受=0，整改=1（不接受）
RISK_LABELS={'accepted':0,'rectification':1}
# 全局训练样本与同模具分组验证的最小条数，不足即拦截、不出概率
RISK_MIN_TOTAL=20
RISK_MIN_PER_MOULD=6
RISK_DEFAULT_THRESHOLD=0.5
