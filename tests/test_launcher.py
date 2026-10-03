import io
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from auto_trader import launcher


class LauncherTests(unittest.TestCase):
    def test_existing_server_requires_same_workspace_and_ready_database(self):
        payload = {'application': 'FOLIO', 'workspace_id': launcher.workspace_id(), 'ready': True}
        opener = MagicMock()
        response = opener.open.return_value.__enter__.return_value
        response.read.side_effect = lambda: __import__('json').dumps(payload).encode()
        with patch.object(launcher, 'build_opener', return_value=opener), patch.object(launcher, 'port_open', return_value=True):
            self.assertEqual(launcher.server_status(8000), 'ready')
            payload['workspace_id'] = 'different-folder'
            self.assertEqual(launcher.server_status(8000), 'other')
            payload['workspace_id'] = launcher.workspace_id()
            payload['ready'] = False
            self.assertEqual(launcher.server_status(8000), 'waiting')

    def test_duplicate_launch_does_not_touch_db_or_start_process(self):
        with patch.object(launcher, 'server_status', return_value='ready'), \
             patch.object(launcher, 'ensure_database') as database, \
             patch.object(launcher.subprocess, 'Popen') as process, \
             patch.object(launcher.webbrowser, 'open') as browser:
            launcher.run()
            database.assert_not_called()
            process.assert_not_called()
            browser.assert_called_once()

    def test_occupied_port_does_not_terminate_foreign_server(self):
        with patch.object(launcher, 'server_status', return_value='other'), patch.object(launcher.subprocess, 'Popen') as process:
            with self.assertRaises(launcher.LaunchError):
                launcher.run(False)
            process.assert_not_called()

    def test_local_stopped_db_starts_service_and_checks_login(self):
        settings = SimpleNamespace(postgres_ready=True, postgres_host='127.0.0.1', postgres_port=5432)
        connect = MagicMock()
        with patch.object(launcher, 'port_open', return_value=False), patch.object(launcher, 'start_database_service') as start:
            launcher.ensure_database(settings, connect)
            start.assert_called_once()
            connect.return_value.__enter__.return_value.execute.assert_called_once_with('SELECT 1')

    def test_remote_db_and_password_failure_are_safe(self):
        settings = SimpleNamespace(postgres_ready=True, postgres_host='db.example', postgres_port=5432)
        with patch.object(launcher, 'port_open', return_value=False), patch.object(launcher, 'start_database_service') as start:
            with self.assertRaises(launcher.LaunchError):
                launcher.ensure_database(settings, Mock())
            start.assert_not_called()
        with patch.object(launcher, 'port_open', return_value=True), patch.object(launcher.time, 'sleep'):
            with self.assertRaises(launcher.LaunchError) as error:
                launcher.ensure_database(settings, Mock(side_effect=RuntimeError('password=do-not-print')))
            self.assertNotIn('do-not-print', str(error.exception))

    def test_service_selection_never_guesses_multiple_installations(self):
        self.assertEqual(launcher.postgres_service(['postgresql-x64-18']), 'postgresql-x64-18')
        self.assertEqual(launcher.postgres_service(['postgresql-x64-17', 'postgresql-x64-18'], 'postgresql-x64-18'), 'postgresql-x64-18')
        for names, selection in [([], None), (['postgresql-x64-17', 'postgresql-x64-18'], None), (['postgresql-x64-18'], 'missing')]:
            with self.assertRaises(launcher.LaunchError):
                launcher.postgres_service(names, selection)

    def test_account_creation_only_on_first_run(self):
        connect, initialize = MagicMock(), Mock()
        query = connect.return_value.__enter__.return_value.execute
        with patch.object(launcher.subprocess, 'run') as run:
            query.return_value.fetchone.return_value = {'present': True}
            launcher.prepare_account(connect, initialize)
            run.assert_not_called()
            query.return_value.fetchone.return_value = {'present': False}
            run.return_value.returncode = 0
            launcher.prepare_account(connect, initialize)
            self.assertIn('auto_trader.create_admin', run.call_args.args[0])

    def test_failed_child_start_and_timeout(self):
        child = Mock()
        child.poll.return_value = 1
        with self.assertRaises(launcher.LaunchError):
            launcher.wait_server(child, 8000)
        child.poll.return_value = None
        with patch.object(launcher.time, 'monotonic', side_effect=[0, 2]):
            with self.assertRaises(launcher.LaunchError):
                launcher.wait_server(child, 8000, timeout=1)

    def test_launch_lock_releases_without_deleting_other_files(self):
        with TemporaryDirectory() as directory, patch.object(launcher, 'ROOT', Path(directory)):
            with launcher.launch_lock():
                with self.assertRaises(launcher.LaunchError):
                    with launcher.launch_lock():
                        pass
            with launcher.launch_lock():
                pass

    def test_interrupt_stops_only_its_own_child(self):
        child = Mock()
        child.poll.return_value = None
        with patch.object(launcher.os, 'name', 'nt'):
            launcher.stop_server(child)
        child.send_signal.assert_called_once()
        child.wait.assert_called_once_with(timeout=15)
        child.kill.assert_not_called()


if __name__ == '__main__':
    unittest.main()
