"""notifier test fixtures. Database tests need TEST_DATABASE_URL (a throwaway PostgreSQL database that the tests
may wipe); they are skipped without it."""
from __future__ import annotations

import os
import socketserver
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
for path in ("libs/llmops-common/src", "components/db-migrate/src", "components/notifier/src"):
    if str(ROOT / path) not in sys.path:
        sys.path.insert(0, str(ROOT / path))
MIGRATIONS = ROOT / "contracts/db/migrations"


@pytest.fixture(scope="session")
def database_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    return url


@pytest.fixture()
def db(database_url, monkeypatch):
    """Fresh schema for every test: drop and re-apply all migrations."""
    import psycopg
    from psycopg.rows import dict_row

    from db_migrate.__main__ import migrate
    from llmops_common import db as dbmod

    with psycopg.connect(database_url, autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS platform CASCADE; DROP SCHEMA IF EXISTS requests CASCADE; "
                  "DROP SCHEMA IF EXISTS tests CASCADE; DROP TABLE IF EXISTS public.llmops_schema_migrations")
    with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as c:
        migrate(c, MIGRATIONS)
    monkeypatch.setenv("DATABASE_URL", database_url)
    dbmod.close_pool()
    yield dbmod
    dbmod.close_pool()


class _SMTPHandler(socketserver.StreamRequestHandler):
    def _say(self, line: str) -> None:
        self.wfile.write((line + "\r\n").encode())

    def handle(self):
        srv = self.server
        self._say("220 fake.smtp ESMTP")
        mail_from, rcpts = None, []
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            cmd = raw.decode("utf-8", "replace").strip()
            up = cmd.upper()
            if up.startswith(("EHLO", "HELO")):
                self.wfile.write(b"250-fake.smtp\r\n250-8BITMIME\r\n250 SMTPUTF8\r\n")
            elif up.startswith("MAIL FROM"):
                mail_from = cmd[10:].strip()
                self._say("250 OK")
            elif up.startswith("RCPT TO"):
                rcpts.append(cmd[8:].strip().strip("<>"))
                self._say(srv.rcpt_reply)
            elif up == "DATA":
                self._say("354 go ahead")
                lines = []
                while True:
                    line = self.rfile.readline()
                    if line in (b".\r\n", b".\n", b""):
                        break
                    lines.append(line)
                if srv.data_reply.startswith("250"):
                    srv.messages.append((mail_from, list(rcpts), b"".join(lines)))
                self._say(srv.data_reply)
            elif up == "QUIT":
                self._say("221 bye")
                return
            else:
                self._say("250 OK")


class FakeSMTPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _SMTPHandler)
        self.messages = []
        self.rcpt_reply = "250 OK"
        self.data_reply = "250 queued"


@pytest.fixture()
def smtp_server():
    srv = FakeSMTPServer()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()
