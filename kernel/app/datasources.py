"""Datasource query execution — one engine per kind.

All drivers are synchronous; calls run in a worker thread so the event loop
never blocks. Queries are read-only by contract: anything that is not a
SELECT/WITH is refused before touching the database.
"""

import json
import re
import sqlite3
import tempfile

import anyio

_READONLY = re.compile(r"^\s*(--[^\n]*\n|\s)*(select|with)\b", re.IGNORECASE)


def ensure_readonly(sql: str) -> None:
    if not _READONLY.match(sql or ""):
        raise ValueError("apenas consultas SELECT/WITH são permitidas nesta tool")


def _rows_to_python(rows) -> list[list]:
    return [[value for value in row] for row in rows]


def _query_postgresql(config: dict, secret: str | None, sql: str, max_rows: int):
    import psycopg

    conninfo = psycopg.conninfo.make_conninfo(
        host=config.get("host", "localhost"),
        port=int(config.get("port", 5432)),
        dbname=config.get("database", ""),
        user=config.get("user", ""),
        password=secret or "",
        connect_timeout=10,
    )
    with psycopg.connect(conninfo) as conn:
        conn.read_only = True
        with conn.cursor() as cursor:
            cursor.execute(sql)
            columns = [
                {"name": d.name, "type": str(d.type_code)} for d in cursor.description
            ]
            rows = cursor.fetchmany(max_rows)
    return columns, _rows_to_python(rows)


def _query_mysql(config: dict, secret: str | None, sql: str, max_rows: int):
    import pymysql

    conn = pymysql.connect(
        host=config.get("host", "localhost"),
        port=int(config.get("port", 3306)),
        database=config.get("database", ""),
        user=config.get("user", ""),
        password=secret or "",
        connect_timeout=10,
        read_timeout=60,
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            columns = [{"name": d[0], "type": str(d[1])} for d in cursor.description]
            rows = cursor.fetchmany(max_rows)
    finally:
        conn.close()
    return columns, _rows_to_python(rows)


def _query_sqlite(config: dict, _secret: str | None, sql: str, max_rows: int):
    conn = sqlite3.connect(config.get("path", ":memory:"), timeout=10)
    try:
        cursor = conn.execute(sql)
        columns = [{"name": d[0], "type": ""} for d in cursor.description or []]
        rows = cursor.fetchmany(max_rows)
    finally:
        conn.close()
    return columns, _rows_to_python(rows)


def _bigquery_client(config: dict, secret: str | None):
    from google.cloud import bigquery
    from google.oauth2 import service_account

    project = config.get("project", "")
    if secret:
        info = json.loads(secret)
        credentials = service_account.Credentials.from_service_account_info(info)
        return bigquery.Client(project=project or info.get("project_id"), credentials=credentials)
    return bigquery.Client(project=project or None)


def _query_bigquery(config: dict, secret: str | None, sql: str, max_rows: int):
    client = _bigquery_client(config, secret)
    job = client.query(sql)
    result = job.result(max_results=max_rows)
    columns = [{"name": f.name, "type": f.field_type} for f in result.schema]
    rows = [[value for value in row.values()] for row in result]
    return columns, rows


_ENGINES = {
    "postgresql": _query_postgresql,
    "mysql": _query_mysql,
    "sqlite": _query_sqlite,
    "bigquery": _query_bigquery,
}


async def execute_query(datasource: dict, sql: str, max_rows: int):
    """(columns, rows) for a read-only query against the datasource."""
    ensure_readonly(sql)
    engine = _ENGINES.get(datasource["kind"])
    if engine is None:
        raise ValueError(f"tipo de datasource não suportado: {datasource['kind']}")
    return await anyio.to_thread.run_sync(
        lambda: engine(datasource.get("config", {}), datasource.get("secret"), sql, max_rows)
    )


_LIST_TABLES_SQL = {
    "postgresql": """
        SELECT table_schema || '.' || table_name, column_name, data_type
          FROM information_schema.columns
         WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
         ORDER BY 1, ordinal_position LIMIT 500""",
    "mysql": """
        SELECT CONCAT(table_schema, '.', table_name), column_name, data_type
          FROM information_schema.columns
         WHERE table_schema = DATABASE()
         ORDER BY 1, ordinal_position LIMIT 500""",
}


async def list_tables(datasource: dict) -> list[dict]:
    """[{table, columns:[{name,type}]}] — capped, for the model's orientation."""
    kind = datasource["kind"]
    if kind in _LIST_TABLES_SQL:
        columns, rows = await execute_query(
            datasource, _LIST_TABLES_SQL[kind], max_rows=500
        )
        tables: dict[str, list] = {}
        for table, column, data_type in rows:
            tables.setdefault(table, []).append({"name": column, "type": data_type})
        return [{"table": t, "columns": c} for t, c in tables.items()]

    if kind == "sqlite":
        columns, rows = await execute_query(
            datasource,
            "SELECT name FROM sqlite_master WHERE type = 'table' LIMIT 100",
            max_rows=100,
        )
        out = []
        for (table,) in rows:
            _, info = await execute_query(
                datasource, f"SELECT * FROM pragma_table_info('{table}')", max_rows=100
            )
            out.append(
                {"table": table, "columns": [{"name": r[1], "type": r[2]} for r in info]}
            )
        return out

    if kind == "bigquery":
        def _bq():
            client = _bigquery_client(
                datasource.get("config", {}), datasource.get("secret")
            )
            dataset_id = datasource.get("config", {}).get("dataset", "")
            out = []
            datasets = (
                [client.get_dataset(dataset_id)] if dataset_id else list(client.list_datasets())[:5]
            )
            for ds in datasets:
                ref = ds.reference if hasattr(ds, "reference") else ds
                for table in list(client.list_tables(ref))[:50]:
                    schema = client.get_table(table.reference).schema
                    out.append(
                        {
                            "table": f"{table.dataset_id}.{table.table_id}",
                            "columns": [{"name": f.name, "type": f.field_type} for f in schema],
                        }
                    )
            return out

        return await anyio.to_thread.run_sync(_bq)

    raise ValueError(f"tipo de datasource não suportado: {kind}")


async def test_connection(datasource: dict) -> tuple[bool, str]:
    try:
        await execute_query(datasource, "SELECT 1", max_rows=1)
        return True, ""
    except Exception as exc:  # noqa: BLE001 — the point is reporting it
        return False, str(exc)[:500]


def make_temp_sqlite(rows_sql: str) -> str:
    """Test helper: temp sqlite database seeded with the given SQL script."""
    path = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(path)
    conn.executescript(rows_sql)
    conn.commit()
    conn.close()
    return path
