from sqlalchemy import Engine, create_engine, event

from buildsize.config import Config


def make_engine(config: Config) -> Engine:
    options = {"check_same_thread": False} if config.database_url.startswith("sqlite") else {}
    engine = create_engine(
        config.database_url, connect_args=options, pool_pre_ping=True, hide_parameters=True
    )
    if config.database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def foreign_keys(connection: object, record: object) -> None:
            from sqlite3 import Connection

            if isinstance(connection, Connection):
                connection.execute("PRAGMA foreign_keys=ON")

    return engine
