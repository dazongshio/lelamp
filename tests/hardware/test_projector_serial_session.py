import importlib.util
import os
from pathlib import Path
import pty
import select
import termios
import threading
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2] / 'scripts/hardware/projector_serial_session.py'
spec = importlib.util.spec_from_file_location('projector_serial_session', SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SerialSessionTests(unittest.TestCase):
    def test_real_tty_uses_9600_8n1_and_transmits_fixed_frame(self):
        master, slave = pty.openpty()
        try:
            module.configure_serial(slave)
            attrs = termios.tcgetattr(slave)
            self.assertEqual(attrs[4:6], [termios.B9600, termios.B9600])
            self.assertFalse(attrs[2] & (termios.PARENB | termios.CSTOPB | termios.HUPCL))
            self.assertFalse(attrs[0] & (termios.IXON | termios.IXOFF))
            received = []
            def read_frame():
                if select.select([master], [], [], 2)[0]:
                    received.append(os.read(master, 8))
            reader = threading.Thread(target=read_frame, daemon=True)
            reader.start()
            result, stop = module.handle_request({'action': 'send', 'command': 'flip-both'}, slave, module.new_state('pty'))
            reader.join(timeout=3)
            self.assertEqual(received, [module.FRAMES['flip-both']])
            self.assertEqual(result['written'], 8)
            self.assertFalse(stop)
        finally:
            os.close(slave)
            os.close(master)

    def test_partial_writes_finish_frame(self):
        payload = module.FRAMES['toggle']
        with patch.object(module.os, 'write', side_effect=[3, 5]) as write:
            self.assertEqual(module.write_all(9, payload), 8)
            self.assertEqual(write.call_args_list[1].args, (9, payload[3:]))

    def test_zero_write_fails(self):
        with patch.object(module.os, 'write', return_value=0):
            with self.assertRaises(OSError):
                module.write_all(9, module.FRAMES['toggle'])

    def test_backpressure_is_retried(self):
        with patch.object(module.os, 'write', side_effect=[BlockingIOError(), 8]), patch.object(module.select, 'select', return_value=([], [9], [])):
            self.assertEqual(module.write_all(9, module.FRAMES['toggle']), 8)

    def test_send_records_host_write_without_inventing_ack(self):
        state = module.new_state('device')
        with patch.object(module, 'write_all', return_value=8) as write, patch.object(module.termios, 'tcdrain'):
            result, stop = module.handle_request({'action': 'send', 'command': 'flip-both'}, 9, state)
        write.assert_called_once_with(9, bytes.fromhex('ff 07 37 00 00 00 00 3e'))
        self.assertFalse(stop)
        self.assertEqual(result['written'], 8)
        self.assertEqual(result['device_state'], 'unknown')

    def test_unknown_command_does_not_write(self):
        with patch.object(module, 'write_all') as write:
            with self.assertRaises(ValueError):
                module.handle_request({'action': 'send', 'command': 'power-on'}, 9, module.new_state('device'))
        write.assert_not_called()

    def test_malformed_request_does_not_close_session(self):
        state = module.new_state('device')
        with patch.object(module, 'write_all') as write:
            reply, stop = module.dispatch({'action': 'send', 'command': []}, 9, state)
        self.assertFalse(reply['ok'])
        self.assertFalse(stop)
        write.assert_not_called()
        reply, stop = module.dispatch({'action': 'status'}, 9, state)
        self.assertTrue(reply['result']['serial_held'])

    def test_drain_error_keeps_holder_and_reports_unknown(self):
        with patch.object(module, 'write_all', return_value=8), patch.object(module.termios, 'tcdrain', side_effect=termios.error(5, 'I/O error')):
            reply, stop = module.dispatch({'action': 'send', 'command': 'toggle'}, 9, module.new_state('device'))
        self.assertFalse(reply['ok'])
        self.assertFalse(stop)
        self.assertEqual(reply['device_state'], 'unknown')

    def test_status_never_sends_power_frame(self):
        with patch.object(module, 'write_all') as write:
            result, stop = module.handle_request({'action': 'status'}, 9, module.new_state('device'))
        write.assert_not_called()
        self.assertEqual(result['device_state'], 'unknown')
        self.assertFalse(stop)

    def test_stop_requires_observed_standby_and_waits_15_seconds(self):
        state = module.new_state('device')
        with self.assertRaises(ValueError):
            module.handle_request({'action': 'stop'}, 9, state)
        with patch.object(module.time, 'sleep') as sleep:
            result, stop = module.handle_request({'action': 'stop', 'confirm_standby': True}, 9, state)
        sleep.assert_called_once_with(15)
        self.assertTrue(stop)
        self.assertEqual(result['standby_confirmed_by'], 'operator')


if __name__ == '__main__':
    unittest.main()
