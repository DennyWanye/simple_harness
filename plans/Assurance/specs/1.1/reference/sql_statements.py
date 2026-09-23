"""Reference statement iterator. Production reuses the reviewed runner fix."""
import sqlite3

def iter_sql_statements(script: str):
    buffer = ''
    for ch in script:
        buffer += ch
        if ch == ';' and sqlite3.complete_statement(buffer):
            yield buffer
            buffer = ''
    # sqlite3.execute accepts a trailing comment; it must not accept incomplete SQL.
    tail = buffer.strip()
    if tail:
        trial = tail + '\n;'
        if sqlite3.complete_statement(trial):
            yield trial
        else:
            raise ValueError('INCOMPLETE_SQL')
