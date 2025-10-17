from __future__ import annotations

from unittest import mock

from django.core.management import CommandError
from django.test import TestCase

from forum import tasks


class TaskTests(TestCase):
    @mock.patch("forum.tasks._scheduler_config", return_value={"interval": 30, "jitter": 0, "queue_burst": 0})
    @mock.patch("forum.tasks.process_generation_burst.delay")
    @mock.patch("forum.tasks.call_command")
    @mock.patch("forum.tasks.tick_control.consume_manual_override", return_value={"seed": 9, "force": True, "origin": "manual"})
    @mock.patch("forum.tasks.tick_control.is_frozen", return_value=False)
    def test_run_scheduled_tick_invokes_run_tick(
        self,
        is_frozen_mock,
        consume_mock,
        call_command_mock,
        delay_mock,
        scheduler_mock,
    ) -> None:
        result = tasks.run_scheduled_tick()
        self.assertEqual(result["status"], "ok")
        call_command_mock.assert_called_once_with("run_tick", origin="manual", seed=9, force=True)
        delay_mock.assert_not_called()
        is_frozen_mock.assert_called_once()
        consume_mock.assert_called_once()
        scheduler_mock.assert_called_once()

    @mock.patch("forum.tasks._scheduler_config", return_value={"interval": 30, "jitter": 0, "queue_burst": 4})
    @mock.patch("forum.tasks.process_generation_burst.delay")
    @mock.patch("forum.tasks.call_command")
    @mock.patch("forum.tasks.tick_control.consume_manual_override", return_value={})
    @mock.patch("forum.tasks.tick_control.is_frozen", return_value=False)
    def test_run_scheduled_tick_triggers_generation_burst(
        self,
        _is_frozen,
        _consume,
        call_command_mock,
        delay_mock,
        scheduler_mock,
    ) -> None:
        tasks.run_scheduled_tick()
        call_command_mock.assert_called_once_with("run_tick", origin="celery")
        delay_mock.assert_called_once()
        delay_mock.assert_called_with(limit=4)
        scheduler_mock.assert_called_once()

    @mock.patch("forum.tasks.tick_control.state_label", return_value="FROZEN")
    @mock.patch("forum.tasks.tick_control.is_frozen", return_value=True)
    def test_run_scheduled_tick_skips_when_frozen(self, is_frozen_mock, state_label_mock) -> None:
        result = tasks.run_scheduled_tick()
        self.assertEqual(result["skipped"], "FROZEN")
        is_frozen_mock.assert_called_once()
        self.assertEqual(state_label_mock.call_count, 2)

    @mock.patch("forum.tasks._scheduler_config", return_value={"queue_burst": 0})
    @mock.patch("forum.tasks.call_command")
    def test_process_generation_burst_noop_when_limit_zero(self, call_command_mock, scheduler_mock) -> None:
        result = tasks.process_generation_burst()
        self.assertEqual(result["status"], "noop")
        call_command_mock.assert_not_called()
        scheduler_mock.assert_called_once()

    @mock.patch("forum.tasks._scheduler_config", return_value={"queue_burst": 3})
    @mock.patch("forum.tasks.call_command")
    def test_process_generation_burst_invokes_command(self, call_command_mock, scheduler_mock) -> None:
        result = tasks.process_generation_burst()
        self.assertEqual(result["status"], "processed")
        self.assertEqual(result["limit"], 3)
        call_command_mock.assert_called_once_with("process_generation_queue", limit=3)
        scheduler_mock.assert_called_once()

    @mock.patch("forum.tasks._scheduler_config", return_value={"queue_burst": 2})
    @mock.patch("forum.tasks.call_command", side_effect=CommandError("no capacity"))
    def test_process_generation_burst_handles_command_error(self, call_command_mock, scheduler_mock) -> None:
        result = tasks.process_generation_burst()
        self.assertEqual(result["status"], "skipped")
        self.assertIn("reason", result)
        call_command_mock.assert_called_once_with("process_generation_queue", limit=2)
        scheduler_mock.assert_called_once()
