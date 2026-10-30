"""LangGraph's Postgres checkpointer on the application database (D-002)."""

from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import Connection
from psycopg.rows import dict_row
from sqlalchemy import make_url


def libpq_url(sqlalchemy_url: str) -> str:
    """postgresql+psycopg://... -> postgresql://... (what psycopg expects)."""
    return (
        make_url(sqlalchemy_url).set(drivername="postgresql").render_as_string(hide_password=False)
    )


@contextmanager
def postgres_checkpointer(sqlalchemy_url: str) -> Iterator[PostgresSaver]:
    with Connection.connect(
        libpq_url(sqlalchemy_url), autocommit=True, prepare_threshold=0, row_factory=dict_row
    ) as connection:
        saver = PostgresSaver(connection)
        saver.setup()
        yield saver
