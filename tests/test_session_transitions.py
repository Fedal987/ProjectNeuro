"""
    ProjectNeuro
    author@Fedal987
    Powered by HeronStudio
    GitHub: https://github.com/Fedal987/ProjectNeuro
"""

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.main.api.api_manager import create_runtime
from src.main.config import APIConfig, AppConfig
from src.main.msg.message_handler import MessageHandler
from src.main.msg.session_manager import SessionManager
from src.main.ui.terminal_cli import build_bottom_toolbar


class SessionTransitionTests(unittest.TestCase):
    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.root = Path(temp_dir.name)
        self.runtime = create_runtime(
            AppConfig(APIConfig('https://example.invalid/v1', 'test', 'test-model')),
            provider=Mock(),
        )
        self.addCleanup(self.runtime.close)
        factory = lambda: MessageHandler(runtime=self.runtime)
        seed = SessionManager(session_factory=factory, storage_root=self.root,
                              workspace_provider=lambda: self.root)
        seed.create_session('saved', switch=False)
        seed.close()
        self.manager = SessionManager(session_factory=factory, storage_root=self.root,
                                      workspace_provider=lambda: self.root)
        self.addCleanup(self.manager.close)

    def test_current_session_survives_background_loading_and_toolbar_redraw(self):
        previous = self.manager.current_session
        entered, release = threading.Event(), threading.Event()
        resume = self.manager.store.resume_session
        errors = []

        def slow_resume(session_id):
            entered.set()
            if not release.wait(3):
                raise AssertionError('test did not release loading')
            return resume(session_id)

        def switch():
            try:
                self.manager.switch_session('saved')
            except Exception as exc:
                errors.append(exc)

        with patch.object(self.manager.store, 'resume_session', side_effect=slow_resume):
            worker = threading.Thread(target=switch)
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                self.assertIs(self.manager.current_session, previous)
                toolbar = build_bottom_toolbar(self.manager.current_handler_if_loaded,
                                               runtime=self.runtime)
                self.assertIn('test-model', toolbar)
            finally:
                release.set()
                worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.manager.current_name, 'saved')
        self.assertNotIn(previous, self.manager.list_sessions())

    def test_failed_resume_preserves_temporary_session(self):
        previous = self.manager.current_session
        with patch.object(self.manager.store, 'resume_session', side_effect=ValueError('broken rollout')):
            with self.assertRaisesRegex(ValueError, 'broken rollout'):
                self.manager.switch_session('saved')
        self.assertIs(self.manager.current_session, previous)
        self.assertIs(self.manager.current_handler_if_loaded, previous.handler)

    def test_failed_target_save_preserves_current_session(self):
        previous = self.manager.current_session
        with patch.object(self.manager, 'save_session', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.manager.switch_session('saved')
        self.assertIs(self.manager.current_session, previous)

    def test_failed_new_session_preserves_temporary_session(self):
        for stage in ('_session_factory', 'save_session'):
            with self.subTest(stage=stage):
                previous = self.manager.current_session
                with patch.object(self.manager, stage, side_effect=OSError('failed')):
                    with self.assertRaises(OSError):
                        self.manager.create_session('new')
                self.assertIs(self.manager.current_session, previous)
                self.assertNotIn('new', [s.name for s in self.manager.list_sessions()])

    def test_new_session_can_replace_temporary_with_same_name(self):
        previous = self.manager.current_session
        selected = self.manager.create_session(previous.name)
        self.assertIs(self.manager.current_session, selected)
        self.assertIsNot(selected, previous)
        self.assertFalse(selected.temporary)
        self.assertTrue(selected.persisted)

    def test_toolbar_snapshot_does_not_load_sessions(self):
        with patch.object(self.manager, '_ensure_handler', side_effect=AssertionError('render must not load')):
            self.manager._current_name = 'saved'
            self.assertIsNone(self.manager.current_handler_if_loaded)
            self.assertIn('test-model', build_bottom_toolbar(
                self.manager.current_handler_if_loaded, runtime=self.runtime))
            self.manager._current_name = None
            self.assertIsNone(self.manager.current_handler_if_loaded)
