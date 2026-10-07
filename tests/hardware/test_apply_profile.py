"""Boot profile changes must preserve Ubuntu boot choices and unrelated devices."""

from contextlib import redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
IMPLEMENTATION = ROOT / "scripts/hardware/apply_profile.py"


class BootProfileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not IMPLEMENTATION.is_file():
            return
        spec = spec_from_file_location("apply_profile", IMPLEMENTATION)
        cls.profile = module_from_spec(spec)
        spec.loader.exec_module(cls.profile)

    def merge(self, text):
        self.assertTrue(IMPLEMENTATION.is_file(), "Boot profile merge is not implemented")
        return self.profile.merge_boot_config(text)

    def test_preserves_ubuntu_os_prefix_and_unrelated_configuration(self):
        original = (
            "[all]\nos_prefix=current/\ncmdline=cmdline.txt\n"
            "[pi5]\ndtoverlay=vc4-kms-v3d\ndtparam=pciex1\n"
            "[all]\ndtoverlay=spi1-1cs\n"
        )
        result = self.merge(original)
        for original_line in ("os_prefix=current/", "cmdline=cmdline.txt", "dtparam=pciex1", "dtoverlay=spi1-1cs"):
            self.assertIn(original_line + "\n", result)
        self.assertIn("[all]\ncamera_auto_detect=0\n", result)

    def test_replaces_active_camera_profiles_and_preserves_inactive_pi4(self):
        original = (
            "camera_auto_detect=1\ndtoverlay=imx219,cam0\n"
            "[pi4]\ndtoverlay=vc4-kms-v3d\ncamera_auto_detect=1\n"
            "[pi5]\ndtoverlay=imx290,cam0\ndtoverlay=vc4-kms-v3d\n"
            "[all]\ndtoverlay=imx219\n"
        )
        result = self.merge(original)
        self.assertIn("[pi4]\ndtoverlay=vc4-kms-v3d\ncamera_auto_detect=1\n", result)
        self.assertNotIn("dtoverlay=imx219,cam0", result)
        self.assertEqual(result.count("dtoverlay=imx290,cam0"), 1)
        self.assertEqual(result.count("dtoverlay=imx219\n"), 1)
        self.assertEqual(result.count("dtoverlay=vc4-kms-v3d,noaudio"), 1)

    def test_idempotence(self):
        first = self.merge("[all]\nos_prefix=current/\n")
        self.assertEqual(first, self.merge(first))

    def test_preserves_comments_and_empty_lines(self):
        original = "# Keep this comment\n\n# dtoverlay=imx219\n[all]\n# camera_auto_detect=1\n"
        result = self.merge(original)
        self.assertTrue(result.startswith(original))

    def test_keeps_other_vc4_options(self):
        result = self.merge("[pi5]\ndtoverlay=vc4-kms-v3d,cma-512,noaudio\n")
        self.assertIn("dtoverlay=vc4-kms-v3d,cma-512,noaudio\n", result)
        self.assertEqual(result, self.merge(result))

    def test_refuses_ambiguous_condition_for_managed_directive(self):
        self.merge("[all]\n")
        with self.assertRaisesRegex(ValueError, "condition"):
            self.profile.merge_boot_config("[gpio4=1]\ndtoverlay=imx219\n")

    def test_refuses_active_include_to_avoid_duplicate_camera_overlays(self):
        self.merge("[all]\n")
        with self.assertRaisesRegex(ValueError, "include"):
            self.profile.merge_boot_config("[all]\ninclude usercfg.txt\n")

    def test_preserves_inactive_include(self):
        result = self.merge("[none]\ninclude disabled-config.txt\n[all]\nos_prefix=current/\n")
        self.assertIn("[none]\ninclude disabled-config.txt\n", result)

    def test_refuses_conflicting_vc4_options(self):
        self.merge("[all]\n")
        with self.assertRaisesRegex(ValueError, "vc4"):
            self.profile.merge_boot_config(
                "[pi5]\ndtoverlay=vc4-kms-v3d,cma-512\n[all]\ndtoverlay=vc4-kms-v3d,cma-256\n"
            )

    def test_unclosed_managed_block_is_rejected(self):
        self.merge("[all]\n")
        with self.assertRaisesRegex(ValueError, "closing marker"):
            self.profile.merge_boot_config(self.profile.BEGIN + "\n[all]\n")

    def test_cli_default_is_read_only(self):
        self.merge("[all]\n")
        (ROOT / "tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as directory:
            target = Path(directory) / "config.txt"
            original = "[all]\nos_prefix=current/\n"
            target.write_text(original)
            output = StringIO()
            with redirect_stdout(output):
                result = self.profile.main(["--boot-config", str(target)])
            self.assertEqual(result, 0)
            self.assertIn("Preview only", output.getvalue())
            self.assertEqual(target.read_text(), original)


if __name__ == "__main__":
    unittest.main()
