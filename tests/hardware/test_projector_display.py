"""Hardware-independent checks for the portable native projector display."""
import importlib.util
import pathlib
import sys
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/hardware/projector_display.py"
DISPLAY = None
if SCRIPT.is_file():
    spec = importlib.util.spec_from_file_location("projector_display", SCRIPT)
    DISPLAY = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = DISPLAY
    spec.loader.exec_module(DISPLAY)


class DisplayTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(DISPLAY, "portable native display implementation is missing")
        return DISPLAY

    def test_cea720_has_exact_clock_sync_and_aspect_flags(self):
        d = self.module()
        mode = d.cea720_mode()
        self.assertEqual(mode.clock, 74250)
        self.assertEqual((mode.hdisplay, mode.hsync_start, mode.hsync_end, mode.htotal),
                         (1280, 1390, 1430, 1650))
        self.assertEqual((mode.vdisplay, mode.vsync_start, mode.vsync_end, mode.vtotal),
                         (720, 725, 730, 750))
        self.assertEqual((mode.hskew, mode.vscan, mode.vrefresh), (0, 0, 60))
        self.assertEqual(mode.flags, 0x100005)
        self.assertEqual(mode.type, 1 << 5)
        self.assertTrue(d.matches_cea720(d.mode_dict(mode)))
        mode.hsync_start = 1328
        self.assertFalse(d.matches_cea720(d.mode_dict(mode)))

    def test_crtc_mask_uses_resource_array_index_not_object_id(self):
        d = self.module()
        self.assertEqual(d.choose_crtc([77, 103], [(2, 0)]), 103)
        self.assertEqual(d.choose_crtc([77, 103], [(3, 103)]), 103)
        self.assertEqual(d.choose_crtc([77, 103], [(0, 0), (1, 0)]), 77)
        with self.assertRaisesRegex(RuntimeError, "compatible CRTC"):
            d.choose_crtc([77, 103], [(4, 0)])

    def test_rgb_becomes_bgrx_and_respects_kernel_pitch(self):
        d = self.module()
        rgb = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 10, 20, 30])
        result = d.rgb_to_xr24(rgb, 2, 2, 12)
        self.assertEqual(result, bytes([0, 0, 255, 0, 0, 255, 0, 0, 0, 0, 0, 0,
                                       255, 0, 0, 0, 30, 20, 10, 0, 0, 0, 0, 0]))
        with self.assertRaises(ValueError):
            d.rgb_to_xr24(rgb[:-1], 2, 2, 12)
        with self.assertRaises(ValueError):
            d.rgb_to_xr24(rgb, 2, 2, 7)

    def test_cleanup_failure_still_restores_original_display_manager(self):
        d = self.module()
        calls = []
        def runner(command, **kwargs):
            calls.append(command)
            if command[1] == "is-active":
                return types.SimpleNamespace(returncode=0, stdout="")
            if command[1] == "show":
                return types.SimpleNamespace(returncode=0, stdout="gdm.service\n")
            return types.SimpleNamespace(returncode=0, stdout="")
        class FailedCleanup:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                raise OSError("framebuffer cleanup failed")
        with self.assertRaisesRegex(OSError, "framebuffer cleanup failed"):
            with d.managed_display_manager(True, runner):
                with FailedCleanup():
                    pass
        self.assertIn(["systemctl", "stop", "gdm.service"], calls)
        self.assertEqual(calls[-1], ["systemctl", "start", "gdm.service"])

    def test_display_manager_remains_untouched_without_explicit_opt_in(self):
        d = self.module()
        def forbidden_runner(*args, **kwargs):
            self.fail("display manager must not be queried or changed without opt-in")
        with d.managed_display_manager(False, forbidden_runner) as unit:
            self.assertIsNone(unit)

    def test_failed_stop_still_attempts_service_restore(self):
        d = self.module()
        calls = []
        def runner(command, **kwargs):
            calls.append(command)
            if command[1] == "is-active":
                return types.SimpleNamespace(returncode=0, stdout="")
            if command[1] == "show":
                return types.SimpleNamespace(returncode=0, stdout="gdm.service\n")
            if command[1] == "stop":
                raise OSError("stop timed out")
            return types.SimpleNamespace(returncode=0, stdout="")
        with self.assertRaisesRegex(OSError, "stop timed out"):
            with d.managed_display_manager(True, runner):
                self.fail("must not begin KMS after stopping failed")
        self.assertEqual(calls[-1], ["systemctl", "start", "gdm.service"])

    def test_check_reads_sysfs_without_opening_drm_or_using_master(self):
        d = self.module()
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as directory:
            base = pathlib.Path(directory)
            sysfs = base / "drm"
            card = sysfs / "card6"
            (card / "device/driver").mkdir(parents=True)
            (card / "device/uevent").write_text("DRIVER=vc4-drm\n")
            connector = sysfs / "card6-HDMI-A-1"
            connector.mkdir()
            (connector / "status").write_text("connected\n")
            (connector / "enabled").write_text("enabled\n")
            (connector / "modes").write_text("1024x768\n")
            report = d.check_hardware(None, "HDMI-A-1", sysfs, base / "dev")
            self.assertEqual(report["selected_card"], str(base / "dev/card6"))
            self.assertEqual(report["connector"]["status"], "connected")
            self.assertEqual(report["connector"]["modes"], ["1024x768"])
            self.assertFalse(report["drm_master_acquired"])

    def test_state_directory_cannot_escape_project_tmp(self):
        d = self.module()
        self.assertEqual(d.state_directory(ROOT, ROOT / "tmp/hardware"),
                         (ROOT / "tmp/hardware").resolve())
        with self.assertRaises(ValueError):
            d.state_directory(ROOT, ROOT / "outside")

    def test_cleanup_attempts_every_resource_after_one_failure(self):
        d = self.module()
        calls = []
        def bad():
            calls.append("bad")
            raise OSError("remove failed")
        def good():
            calls.append("good")
        with self.assertRaisesRegex(OSError, "remove failed"):
            d.cleanup_all([bad, good])
        self.assertEqual(calls, ["bad", "good"])

    def test_sighup_is_ignored_and_sigterm_requests_exit_then_handlers_restore(self):
        import signal
        d = self.module()
        self.assertTrue(hasattr(d, "display_signals"), "serve needs persistent SSH-safe signal handling")
        signals = [signal.SIGINT, signal.SIGTERM]
        if hasattr(signal, "SIGHUP"):
            signals.append(signal.SIGHUP)
        previous = {item: signal.getsignal(item) for item in signals}
        with d.display_signals() as should_stop:
            self.assertFalse(should_stop())
            if hasattr(signal, "SIGHUP"):
                self.assertEqual(signal.getsignal(signal.SIGHUP), signal.SIG_IGN)
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            self.assertTrue(should_stop())
        self.assertEqual({item: signal.getsignal(item) for item in signals}, previous)

    def test_native_close_releases_remaining_resources_and_restores_service_after_rmfb_error(self):
        import os
        d = self.module()
        trace = []
        def runner(command, **kwargs):
            if command[1] == "is-active":
                return types.SimpleNamespace(returncode=0, stdout="")
            if command[1] == "show":
                return types.SimpleNamespace(returncode=0, stdout="gdm.service\n")
            trace.append(command[1] + " gdm")
            return types.SimpleNamespace(returncode=0, stdout="")
        class FakeDrm:
            check = staticmethod(d.LibDrm.check)
            def set_crtc(self, *args):
                trace.append("disable crtc")
                return 0
            def remove_fb(self, *args):
                trace.append("remove fb")
                return -5
            def destroy_dumb(self, *args):
                trace.append("destroy buffer")
                return 0
            def free_connector(self, *args):
                trace.append("free connector")
            def free_resources(self, *args):
                trace.append("free resources")
            def drop_master(self, *args):
                trace.append("drop master")
                return 0
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as directory:
            display = d.NativeDisplay.__new__(d.NativeDisplay)
            display.drm = FakeDrm()
            display.fd = os.open(pathlib.Path(directory) / "fd", os.O_RDWR | os.O_CREAT, 0o600)
            fd = display.fd
            display.surface = types.SimpleNamespace(close=lambda: trace.append("unmap"))
            display.previous = None
            display.previous_connectors = []
            display.crtc_id = 77
            display.fb, display.handle = d.u32(10), d.u32(20)
            display.connector_ptr = display.resource_ptr = 1
            display.mode_active = display.master = True
            with self.assertRaises(OSError):
                with d.managed_display_manager(True, runner):
                    display.close()
            with self.assertRaises(OSError):
                os.fstat(fd)
            self.assertIsNone(display.fd)
            display.close()  # Idempotent even after the first cleanup raised.
        self.assertEqual(trace, ["stop gdm", "disable crtc", "remove fb", "unmap",
                                 "destroy buffer", "free connector", "free resources",
                                 "drop master", "start gdm"])

    def test_cli_defaults_and_display_manager_opt_in(self):
        d = self.module()
        args = d.parser(ROOT).parse_args(["serve"])
        self.assertEqual(args.connector, "HDMI-A-1")
        self.assertIsNone(args.card)
        self.assertFalse(args.manage_display_manager)
        self.assertEqual(args.image, ROOT / "assets/hardware/projector-test-card.rgb")
        self.assertTrue(d.parser(ROOT).parse_args(["serve", "--manage-display-manager"]).manage_display_manager)


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(DISPLAY)
        self.old_cwd = pathlib.Path.cwd()
        # Relative Unix socket addresses keep macOS/Linux sun_path short; all
        # temporary files still live under the permitted work project's tmp.
        import os
        os.chdir(ROOT)
        self.temp = tempfile.TemporaryDirectory(dir="tmp")
        self.directory = pathlib.Path(self.temp.name).relative_to(ROOT)

    def tearDown(self):
        import os
        self.temp.cleanup()
        os.chdir(self.old_cwd)

    def test_status_and_stop_confirm_cleanup_before_reply(self):
        import threading
        d = DISPLAY
        trace = []
        errors = []
        with d.ControlServer(self.directory) as control:
            def worker():
                try:
                    control.run({"state": "active", "mode": d.mode_dict(d.cea720_mode())})
                    trace.append("kms cleaned and display manager restored")
                    control.finish({"state": "stopped"})
                except BaseException as error:
                    errors.append(error)
            thread = threading.Thread(target=worker)
            thread.start()
            try:
                active = d.control_request(self.directory, "status")
                self.assertEqual(active["state"], "active")
                self.assertTrue(d.matches_cea720(active["mode"]))
                stopped = d.control_request(self.directory, "stop")
                self.assertEqual(stopped["state"], "stopped")
                self.assertEqual(trace, ["kms cleaned and display manager restored"])
            finally:
                thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
        self.assertFalse((self.directory / d.SOCKET_NAME).exists())

    def test_another_process_cannot_replace_live_socket(self):
        d = DISPLAY
        with d.ControlServer(self.directory):
            with self.assertRaisesRegex(RuntimeError, "Another projector"):
                with d.ControlServer(self.directory):
                    pass
            self.assertTrue((self.directory / d.SOCKET_NAME).is_socket())

    def test_stop_flag_exits_without_interrupting_cleanup(self):
        import inspect
        d = DISPLAY
        self.assertIn("should_stop", inspect.signature(d.ControlServer.run).parameters,
                      "signal handling must request exit instead of throwing during resource restoration")
        # An already-requested exit must not enter select or accept.
        control = d.ControlServer(self.directory)
        control.run({"state": "active"}, should_stop=lambda: True)

    def test_live_socket_without_our_lock_is_not_removed(self):
        import socket
        d = DISPLAY
        path = self.directory / d.SOCKET_NAME
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as existing:
            existing.bind(str(path))
            existing.listen(1)
            with self.assertRaisesRegex(RuntimeError, "already active"):
                with d.ControlServer(self.directory):
                    pass
            self.assertTrue(path.is_socket())

    def test_dangling_symlink_is_preserved(self):
        d = DISPLAY
        path = self.directory / d.SOCKET_NAME
        path.symlink_to("missing-target")
        with self.assertRaisesRegex(RuntimeError, "non-socket"):
            with d.ControlServer(self.directory):
                pass
        self.assertTrue(path.is_symlink())

    def test_stale_socket_can_be_reused(self):
        import socket
        d = DISPLAY
        path = self.directory / d.SOCKET_NAME
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as existing:
            existing.bind(str(path))
        with d.ControlServer(self.directory):
            self.assertTrue(path.is_socket())

    def test_existing_non_socket_is_preserved(self):
        d = DISPLAY
        path = self.directory / d.SOCKET_NAME
        path.write_text("keep me")
        with self.assertRaisesRegex(RuntimeError, "non-socket"):
            with d.ControlServer(self.directory):
                pass
        self.assertEqual(path.read_text(), "keep me")


if __name__ == "__main__":
    unittest.main()
