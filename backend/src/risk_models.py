"""Acceptance risk review gates repeat-rectification moulds before sample dispatch.

训练数据只来自已签收并已关闭的客户首次意见（接受/整改），按模具分组验证；
复核结论与依据在复核当时固化为快照，后补意见不混入既有复核。
估价工时回归（ml.py）与本验收风险分类器完全独立，互不影响。
"""
from sqlalchemy import String, Float, Integer, ForeignKey, JSON, CheckConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


class AcceptanceRiskReview(Base):
    __tablename__ = 'acceptance_risk_reviews'
    __table_args__ = (
        # 每个结算最多一条「有效（未作废）」复核；作废记录保留历史，允许重新发起新建
        Index('ux_risk_review_active', 'review_id', unique=True,
             postgresql_where="state <> 'voided'"),
        CheckConstraint('threshold > 0 AND threshold < 1'),
        CheckConstraint("state IN ('pending','insufficient','blocked','released','dispatched','voided')"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    review_id: Mapped[int] = mapped_column(ForeignKey('cost_reviews.id'))
    mould_id: Mapped[int] = mapped_column(ForeignKey('moulds.id'))
    state: Mapped[str] = mapped_column(String(20), default='pending')
    # 主管设定的拦截阈值；发起复核时快照当前阈值，后续改阈值不影响在途复核
    threshold: Mapped[float] = mapped_column(Float)
    # 不接受（整改）概率，样本不足时为空
    reject_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 复核当时固化的特征、训练集指纹与分组验证依据；后补意见不回写
    basis: Mapped[dict] = mapped_column(JSON)
    initiated_by: Mapped[str] = mapped_column(String(120))
    completed_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # 主管放行说明（仅高风险拦截后放行时填写）
    release_note: Mapped[str] = mapped_column(String(600), default='')
    released_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    released_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    dispatched_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    voided_reason: Mapped[str] = mapped_column(String(600), default='')
    voided_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # 重试计数：中断/失败后重试不新增复核记录，只累加并覆盖同一条
    attempts: Mapped[int] = mapped_column(Integer, default=1)


class RiskThresholdSetting(Base):
    """主管维护的全局拦截阈值，单行（id 固定为 1）。"""
    __tablename__ = 'risk_threshold_settings'
    __table_args__ = (CheckConstraint('threshold > 0 AND threshold < 1'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    threshold: Mapped[float] = mapped_column(Float, default=0.5)
    updated_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    updated_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
