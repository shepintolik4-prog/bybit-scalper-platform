from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.config import get_settings

settings = get_settings()
_db_url = settings.database_url
if _db_url.startswith("sqlite"):
    # FastAPI + фоновые потоки: SQLite требует отключить check_same_thread
    engine = create_engine(
        _db_url,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )

    @event.listens_for(engine, "connect")
    def _sqlite_wal(dbapi_connection, _connection_record) -> None:
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()
else:
    # Частый polling панели + фоновые тики могут исчерпать дефолтный QueuePool (size=5).
    # Поднимаем пул и добавляем recycle, чтобы избежать таймаутов соединений.
    engine = create_engine(
        _db_url,
        pool_pre_ping=True,
        pool_size=15,
        max_overflow=30,
        pool_timeout=30,
        pool_recycle=300,
    )
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
