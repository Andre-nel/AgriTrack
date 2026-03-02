from app.models import Mob


class MobRepository:
    @staticmethod
    def list_by_farm(farm_id: str):
        return Mob.query.filter_by(farm_id=farm_id, status="active").order_by(Mob.name).all()

    @staticmethod
    def active_by_farm(farm_id: str):
        return MobRepository.list_by_farm(farm_id)

    @staticmethod
    def get_or_404(mob_id: str):
        return Mob.query.get_or_404(mob_id)
