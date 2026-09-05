import threading
import unittest

from app.annotation_tasks import (
    _HistoryCleanupSupervisor,
    _history_cleanup_supervisor_loop,
)


class AnnotationHistoryOutboxTests(unittest.TestCase):
    def test_retry_loop_uses_capped_backoff_and_wake_resets_delay(self) -> None:
        stop_event = threading.Event()
        wait_results = [False, False, True, False]
        wait_seconds: list[float] = []
        cleanup_calls = 0

        class RecordingWakeEvent:
            def clear(self) -> None:
                return

            def wait(self, timeout: float) -> bool:
                wait_seconds.append(timeout)
                awakened = wait_results.pop(0)
                if not wait_results:
                    stop_event.set()
                return awakened

        def cleanup() -> bool:
            nonlocal cleanup_calls
            cleanup_calls += 1
            return True

        _history_cleanup_supervisor_loop(
            cleanup,
            stop_event,
            RecordingWakeEvent(),  # type: ignore[arg-type]
            initial_delay_seconds=1.0,
            max_delay_seconds=4.0,
        )

        self.assertEqual(cleanup_calls, 4)
        self.assertEqual(wait_seconds, [1.0, 2.0, 4.0, 1.0])

    def test_supervisor_retries_while_running_and_stops_cleanly(self) -> None:
        retried = threading.Event()
        call_lock = threading.Lock()
        cleanup_calls = 0

        def cleanup() -> bool:
            nonlocal cleanup_calls
            with call_lock:
                cleanup_calls += 1
                current_call = cleanup_calls
            if current_call == 2:
                retried.set()
            return current_call == 1

        supervisor = _HistoryCleanupSupervisor(
            cleanup,
            initial_delay_seconds=0.01,
            max_delay_seconds=0.05,
            shutdown_timeout_seconds=0.5,
        )
        self.assertTrue(supervisor.start())
        self.assertTrue(retried.wait(timeout=1.0))
        self.assertTrue(supervisor.is_alive())
        self.assertTrue(supervisor.stop())
        self.assertFalse(supervisor.is_alive())
        self.assertTrue(supervisor.stop())
        self.assertGreaterEqual(cleanup_calls, 2)


if __name__ == "__main__":
    unittest.main()
