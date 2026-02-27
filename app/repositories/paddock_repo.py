from sqlalchemy import and_

from app.models import Paddock
from app.models.grazing import GrazingAllocation, GrazingSession


class PaddockRepository:
    @staticmethod
    def list_by_farm(farm_id: str):
        return Paddock.query.filter_by(farm_id=farm_id).order_by(Paddock.name).all()

    @staticmethod
    def get_or_404(paddock_id: str):
        return Paddock.query.get_or_404(paddock_id)

    @staticmethod
    def active_allocations(paddock_id: str):
        return (
            GrazingAllocation.query.join(GrazingSession)
            .filter(
                and_(
                    GrazingAllocation.paddock_id == paddock_id,
                    GrazingSession.end_at.is_(None),
                )
            )
            .all()
        )
