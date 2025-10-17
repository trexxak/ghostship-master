from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.test import TestCase

from forum.services import sim_config


class SimConfigTests(TestCase):
    def tearDown(self) -> None:
        sim_config.clear_cache()
        super().tearDown()

    def test_override_path_is_loaded(self) -> None:
        payload = {"version": 7, "scheduler": {"interval_seconds": 22}}
        loaded: dict[str, object] | None = None
        with self.subTest("override"):
            with self._temporary_config(payload) as cfg_path:
                with mock.patch.dict(os.environ, {"SIM_CONFIG_PATH": str(cfg_path)}):
                    sim_config.clear_cache()
                    loaded = sim_config.load_config(force=True)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["version"], 7)
        self.assertEqual(loaded["scheduler"]["interval_seconds"], 22)

    def test_fingerprint_tracks_active_payload(self) -> None:
        sim_config.clear_cache()
        config = sim_config.load_config(force=True)
        fingerprint = sim_config.fingerprint()
        self.assertIn("sha1", fingerprint)
        self.assertEqual(fingerprint["version"], config.get("version", 0))
        self.assertTrue(Path(fingerprint["path"]).exists())

    def test_snapshot_includes_cooldowns_and_scheduler(self) -> None:
        sim_config.clear_cache()
        snap = sim_config.snapshot()
        self.assertIn("cooldowns", snap)
        self.assertIn("scheduler", snap)
        self.assertIn("fingerprint", snap)

    @contextmanager
    def _temporary_config(self, payload: dict) -> Path:
        with TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / "test-sim-config.toml"
            body = [f"version = {payload.get('version', 1)}"]
            scheduler = payload.get("scheduler", {})
            if scheduler:
                body.append("\n[scheduler]")
                for key, value in scheduler.items():
                    body.append(f"{key} = {json.dumps(value)}")
            cfg_path.write_text("\n".join(body), encoding="utf-8")
            yield cfg_path
