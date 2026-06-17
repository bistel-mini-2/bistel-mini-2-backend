from app.db.session import DbSessionDep


# Future authentication dependencies, such as current-user resolution, can live here.

__all__ = ["DbSessionDep"]
