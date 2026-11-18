from typing import Literal
from pydantic import BaseModel, Field, ConfigDict


class Clean(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class RiskReviewInput(Clean):
    review_id: int = Field(gt=0)


class RiskThresholdInput(Clean):
    threshold: float = Field(gt=0, lt=1)


class RiskReleaseInput(Clean):
    release_note: str = Field(min_length=8, max_length=600)


class RiskRetryInput(Clean):
    # 重试不新增复核；保留入参仅为语义清晰，当前固定 review 维度
    review_id: int = Field(gt=0)
