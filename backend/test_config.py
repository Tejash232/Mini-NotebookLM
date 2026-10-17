"""Focused configuration tests; these never create a database connection."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config, database


class EnvironmentConfigurationTests(unittest.TestCase):
    def test_loads_backend_dotenv_independent_of_working_directory(self):
        with tempfile.TemporaryDirectory(prefix="mini-notebooklm-config-test-") as directory:
            backend_dir = Path(directory)
            (backend_dir / ".env").write_text(
                "DATABASE_URL=sqlite:///dotenv-config-test.sqlite3\n",
                encoding="utf-8",
            )
            with patch.object(config, "BACKEND_DIR", backend_dir), patch.dict(os.environ, {}, clear=True):
                self.assertTrue(config.load_environment())
                self.assertEqual(database.database_url(), "sqlite:///dotenv-config-test.sqlite3")

    def test_process_environment_overrides_dotenv_value(self):
        with tempfile.TemporaryDirectory(prefix="mini-notebooklm-config-test-") as directory:
            backend_dir = Path(directory)
            (backend_dir / ".env").write_text(
                "DATABASE_URL=sqlite:///dotenv.sqlite3\n",
                encoding="utf-8",
            )
            with patch.object(config, "BACKEND_DIR", backend_dir), patch.dict(
                os.environ, {"DATABASE_URL": "postgresql://configured-by-platform/db"}, clear=True,
            ):
                config.load_environment()
                self.assertEqual(
                    database.database_url(),
                    "postgresql+psycopg://configured-by-platform/db",
                )

    def test_missing_database_url_fails_before_any_connection_attempt(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "DATABASE_URL is required"):
                database.database_url()


if __name__ == "__main__":
    unittest.main()
