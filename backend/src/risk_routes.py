from fastapi import APIRouter, Depends
from sqlalchemy import select
from .db import db_session
from .auth import current, writers, admin
from .services import serialize
from .risk_models import AcceptanceRiskReview, RiskThresholdSetting
from .risk_schemas import RiskReviewInput, RiskThresholdInput, RiskReleaseInput
from . import risk_service as svc

router = APIRouter()


@router.get('/acceptance-risk-reviews')
def list_reviews(s=Depends(db_session), u=Depends(current)):
    return [svc.view(s, x) for x in
            s.scalars(select(AcceptanceRiskReview).order_by(AcceptanceRiskReview.id.desc()))]


@router.post('/acceptance-risk-reviews')
def initiate(p: RiskReviewInput, s=Depends(db_session), u=Depends(writers)):
    return svc.initiate(s, p, u)


@router.post('/acceptance-risk-reviews/{rid}/release')
def release(rid: int, p: RiskReleaseInput, s=Depends(db_session), u=Depends(admin)):
    return svc.release(s, rid, p, u)


@router.get('/risk-threshold')
def get_threshold(s=Depends(db_session), u=Depends(current)):
    return serialize(svc.get_threshold(s))


@router.put('/risk-threshold')
def set_threshold(p: RiskThresholdInput, s=Depends(db_session), u=Depends(admin)):
    return svc.update_threshold(s, p, u)
