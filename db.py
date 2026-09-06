"""Database helpers. All SQL lives here; app.py stays about pages.

The connection string comes from the environment, never from this file — a
database URL committed to git is a leaked password.
"""
import os
import re
from datetime import date, datetime, time
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row


# --- the connection ---------------------------------------------------------
#
# One connection for the whole process: opened by the first query, reused
# by every query after it. Opening a connection is the slow part — the app
# and the database greet each other four times over the network before a
# single row moves. Paid once per query, a menu page that runs three
# queries pays it three times; paid once per process, every query after
# the first is just the query.
#
# If the line drops — the database closed an idle connection, or the host
# froze the app between two visitors — the next query notices, opens a
# fresh line and runs again. That is safe because a statement sent down a
# dead line never reached the database.

_connection = None


def get_connection():
    """The shared connection: opened on first use, reused after that."""
    global _connection
    if _connection is not None and not _connection.closed:
        return _connection
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Put it in .env.local for your laptop and "
            "in the hosting dashboard for the live site."
        )
    conn = psycopg.connect(url, row_factory=dict_row)
    # Every statement commits by itself. Each helper below runs ONE
    # statement, so a shared connection never carries a half-finished
    # transaction from one request into the next.
    conn.autocommit = True
    _connection = conn
    return conn


def _run(sql, params, fetch):
    """Runs one statement on the shared connection.

    fetch is "all" (a list of rows), "one" (the first row or None) or None
    (the number of rows the statement touched). If the line has dropped
    since the last query, the connection is reopened and the statement is
    run once more.
    """
    global _connection
    for attempt in (1, 2):
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                if fetch == "all":
                    return cur.fetchall()
                if fetch == "one":
                    return cur.fetchone()
                return cur.rowcount
        except (psycopg.errors.OperationalError, psycopg.errors.InterfaceError):
            dropped = bool(getattr(conn, "broken", False)) or bool(conn.closed)
            if attempt == 2 or not dropped:
                raise
            try:
                conn.close()
            except Exception:
                pass
            _connection = None


def query(sql, params=()):
    """Runs a SELECT and returns a list of dictionaries."""
    return _run(sql, params, "all")


def query_one(sql, params=()):
    """Runs a SELECT and returns the first row as a dictionary, or None."""
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql, params=()):
    """Runs an INSERT / UPDATE / DELETE and returns how many rows it touched."""
    return _run(sql, params, None)


def insert_returning(sql, params=()):
    """Runs an INSERT ... RETURNING and gives back the new row.

    Needed when the next statement must reference the row just created —
    an order's lines cannot be written without the order's new id.
    """
    return _run(sql, params, "one")


def init_db():
    """Creates the tables. Safe to run many times.

    On your laptop the schema is schema.sql, next to this file. In the
    repository DoneJi ships, that same file lives at
    supabase/migrations/<version>_init.sql, and every later change to the
    tables is another .sql file beside it. So: schema.sql when it exists;
    otherwise every file in the migrations folder that has not run yet, in
    name order, each one recorded in a small ledger table so the next start
    skips it. Either way, one init_db() on start is enough.
    """
    if os.path.exists("schema.sql"):
        with open("schema.sql", "r", encoding="utf-8") as f:
            execute(f.read())
        _COLUMNS.clear()
        return
    folder = os.path.join("supabase", "migrations")
    if not os.path.isdir(folder):
        raise RuntimeError(
            "Nothing to create the tables from: no schema.sql here and no "
            "supabase/migrations folder."
        )
    execute(
        "create table if not exists _doneji_migrations ("
        "name text primary key, applied_at timestamp not null default now())"
    )
    done = {row["name"] for row in query("select name from _doneji_migrations")}
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".sql") or name in done:
            continue
        with open(os.path.join(folder, name), "r", encoding="utf-8") as f:
            execute(f.read())
        execute("insert into _doneji_migrations (name) values (%s)", (name,))
    _COLUMNS.clear()


# --- forms to rows ----------------------------------------------------------
#
# A browser sends EVERY field as text, and sends "" for a box left empty.
# A column that may be empty wants NULL, an integer column wants an int, a
# date column wants a date, and a NOT NULL column with no value wants a
# polite sentence back to the person — not a database error. Doing that by
# hand in every route is exactly where the '' -> NULL bugs live, so it is
# done once here, from the table's own definition. information_schema is
# the catalogue Postgres keeps about its own tables (CC202 territory).

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")

# table name -> its column list, filled by table_columns, emptied by init_db.
_COLUMNS = {}

_TEXT_TYPES = ("text", "character varying", "character")
_INTEGER_TYPES = ("integer", "smallint", "bigint")
_DECIMAL_TYPES = ("numeric", "real", "double precision")


def table_columns(table):
    """The columns of one table, straight from the catalogue.

    Refuses a name that is not a plain identifier or not a table: the name
    is spliced into SQL by insert_from_form, so it must be one the database
    itself vouches for — never a value a form typed.
    """
    if not isinstance(table, str) or not _IDENTIFIER.match(table):
        raise ValueError("not a table name: %r" % (table,))
    # Asked once per table per process, not once per form: a table's column
    # list does not change while the app runs, and a register page was
    # paying two catalogue round trips for one insert.
    if table in _COLUMNS:
        return _COLUMNS[table]
    cols = query(
        """select column_name, data_type, is_nullable, column_default
             from information_schema.columns
            where table_schema = 'public' and table_name = %s
            order by ordinal_position""",
        (table,),
    )
    if not cols:
        raise ValueError("no table named %s — has init_db() run?" % table)
    _COLUMNS[table] = cols
    return cols


def _label(name):
    """seats -> Seats, collection_date -> Collection date: the words a
    person reads in an error sentence."""
    return name.replace("_", " ").strip().capitalize()


def _cast(text, data_type, label):
    """One form value as the Python type its column wants.

    Returns (value, None) or (None, "sentence") — the sentence is shown to
    the person as it is, so it names the field in their words.
    """
    try:
        if data_type in _INTEGER_TYPES:
            return int(text), None
        if data_type in _DECIMAL_TYPES:
            return Decimal(text), None
        if data_type == "boolean":
            low = text.lower()
            if low in ("1", "true", "t", "on", "yes", "y"):
                return True, None
            if low in ("0", "false", "f", "off", "no", "n"):
                return False, None
            raise ValueError(text)
        if data_type == "date":
            return date.fromisoformat(text), None
        if data_type.startswith("time "):
            return time.fromisoformat(text), None
        if data_type.startswith("timestamp"):
            return datetime.fromisoformat(text), None
    except (ValueError, ArithmeticError):
        wants = {
            "boolean": "yes or no",
            "date": "a date (YYYY-MM-DD)",
        }.get(data_type)
        if wants is None:
            if data_type in _INTEGER_TYPES:
                wants = "a whole number"
            elif data_type in _DECIMAL_TYPES:
                wants = "a number"
            elif data_type.startswith("time "):
                wants = "a time (HH:MM)"
            else:
                wants = "a date and time"
        return None, "%s must be %s." % (label, wants)
    return text, None


def row_from_form(form, table, *, required=(), only=None):
    """Turns a submitted form into a row for `table`, plus a list of errors.

    Returns (row, errors). Only the form's fields that are real columns of
    the table are kept; the serial id and any column the form left out are
    left to their defaults; "" becomes NULL where the column allows it;
    numbers, dates, times and yes/no boxes are converted to their types.
    `required` names columns that must not be empty; `only` restricts which
    columns may come from the form at all (everything else — the logged-in
    user's id, a password hash — the route adds to the row itself).

    Every error is a sentence to show the person: "Seats must be a whole
    number." An empty errors list means the row is safe to insert.
    """
    row = {}
    errors = []
    for col in table_columns(table):
        name = col["column_name"]
        if only is not None and name not in only:
            continue
        default = col["column_default"] or ""
        if "nextval(" in default:
            continue  # the serial id is the database's to invent
        label = _label(name)
        raw = form.get(name)
        if raw is None:
            if name in required:
                errors.append("%s is required." % label)
            elif col["data_type"] == "boolean" and col["is_nullable"] == "NO" and not default:
                row[name] = False  # an unticked checkbox sends nothing at all
            continue
        if not isinstance(raw, str):
            row[name] = raw  # a route passing a real value through, not text
            continue
        text = raw.strip()
        if text == "":
            if name in required:
                errors.append("%s is required." % label)
            elif col["is_nullable"] == "YES":
                row[name] = None  # empty box, column allows empty: NULL, never ''
            elif default:
                pass  # the column's own default fills it
            elif col["data_type"] in _TEXT_TYPES:
                row[name] = ""
            else:
                errors.append("%s is required." % label)
            continue
        value, problem = _cast(text, col["data_type"], label)
        if problem:
            errors.append(problem)
        else:
            row[name] = value
    return row, errors


def insert_from_form(table, row, *, on_conflict_do_nothing=False):
    """Inserts `row` (from row_from_form, plus whatever the route added) into
    `table` and returns {"id": ...} for the new row — or None when
    on_conflict_do_nothing=True and a UNIQUE rule quietly skipped it.

    The table and column names are spliced into the SQL, so both are checked
    against the catalogue first: a name Postgres does not know is refused
    before any SQL is built. The VALUES travel as %s parameters, exactly like
    every other query in this file — that is the line between an identifier
    the database vouches for and a value a stranger typed.
    """
    known = [c["column_name"] for c in table_columns(table)]
    names = list(row.keys())
    for name in names:
        if name not in known:
            raise ValueError("%s has no column named %s" % (table, name))
    if not names:
        raise ValueError("nothing to insert into %s" % table)
    columns = ", ".join('"%s"' % n for n in names)
    marks = ", ".join(["%s"] * len(names))
    sql = 'insert into "%s" (%s) values (%s)' % (table, columns, marks)
    if on_conflict_do_nothing:
        sql += " on conflict do nothing"
    values = [row[n] for n in names]
    if "id" in known:
        return insert_returning(sql + ' returning "id"', values)
    execute(sql, values)
    return None

