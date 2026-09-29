"""Bind filesystem exports to the current dashboard database generation.

Restoring an old SQLite snapshot can rewind AUTOINCREMENT. Numeric account IDs
are therefore only immutable within a generation, not across a restore.
"""
import uuid

import db


SCHEMA = '''CREATE TABLE IF NOT EXISTS export_generation (
    id INTEGER PRIMARY KEY CHECK (id = 1), nonce TEXT NOT NULL)'''


def current():
    with db.transaction() as conn:
        conn.execute(SCHEMA)
        conn.execute('INSERT OR IGNORE INTO export_generation (id,nonce) VALUES (1,?)', (uuid.uuid4().hex,))
        return conn.execute('SELECT nonce FROM export_generation WHERE id=1').fetchone()[0]


def rotate(connection):
    """Rotate inside the staged, verified database before restore publication."""
    connection.execute(SCHEMA)
    connection.execute('INSERT OR REPLACE INTO export_generation (id,nonce) VALUES (1,?)', (uuid.uuid4().hex,))
