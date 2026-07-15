import os
import unittest
from unittest import mock

from autils.devel import lkp


class CloneTest(unittest.TestCase):
    """Unit tests for lkp.clone."""

    @mock.patch("autils.devel.process.run")
    @mock.patch("os.makedirs")
    @mock.patch("os.path.isdir")
    def test_clone_new_checkout(self, isdir, makedirs, run):
        """When dest/.git is absent, clone creates the parent dir and runs git clone."""
        isdir.return_value = False

        result = lkp.clone(
            "https://example.com/lkp-tests.git", "/tmp/lkp-tests", branch="main"
        )

        self.assertEqual(result, os.path.abspath("/tmp/lkp-tests"))
        makedirs.assert_called_once()
        run.assert_called_once()
        cmd = run.call_args.args[0]
        self.assertIn("git clone", cmd)
        self.assertIn("--branch main", cmd)
        self.assertIn("https://example.com/lkp-tests.git", cmd)


class InstallTest(unittest.TestCase):
    """Unit tests for lkp.install."""

    @mock.patch("autils.devel.process.run")
    @mock.patch("os.chmod")
    @mock.patch("builtins.open", new_callable=mock.mock_open)
    @mock.patch("os.path.isfile")
    def test_install_runs_build_and_install_steps(self, isfile, mock_open, chmod, run):
        """install() makes the installer tolerant, then builds and installs."""
        isfile.return_value = True

        lkp_bin = lkp.install("/tmp/lkp-tests")

        self.assertEqual(
            lkp_bin, os.path.join(os.path.abspath("/tmp/lkp-tests"), "bin", "lkp")
        )
        # make subsystem, make install, and bin/lkp install
        self.assertEqual(run.call_count, 3)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn("make -j1 subsystem", commands[0])
        self.assertIn("make -j1 install", commands[1])
        self.assertIn("bin/lkp install", commands[2])


class InstallJobTest(unittest.TestCase):
    """Unit tests for lkp.install_job."""

    @mock.patch("autils.devel.process.run")
    @mock.patch("os.path.isfile")
    @mock.patch("os.makedirs")
    @mock.patch("builtins.open", new_callable=mock.mock_open)
    def test_install_job_splits_and_installs_subjobs(
        self, mock_open, makedirs, isfile, run
    ):
        """Sub-jobs parsed from split-job output are individually installed."""
        isfile.return_value = True
        split_result = mock.Mock(
            stdout_text="=> ./bench-1.yaml\n=> ./bench-2.yaml\n",
            stderr_text="",
        )
        install_result = mock.Mock(stdout_text="", stderr_text="")
        run.side_effect = [split_result, install_result, install_result]

        subs = lkp.install_job("/tmp/lkp-tests", "job.yaml", "my-testbox")

        lkp_dir = os.path.abspath("/tmp/lkp-tests")
        self.assertEqual(
            subs,
            [
                os.path.join(lkp_dir, "bench-1.yaml"),
                os.path.join(lkp_dir, "bench-2.yaml"),
            ],
        )
        self.assertEqual(run.call_count, 3)

    @mock.patch("autils.devel.process.run")
    def test_install_job_rejects_unsafe_testbox_name(self, run):
        """Path traversal characters in the testbox name are rejected."""
        with self.assertRaises(ValueError):
            lkp.install_job("/tmp/lkp-tests", "job.yaml", "../evil")
        run.assert_not_called()


class RunJobTest(unittest.TestCase):
    """Unit tests for lkp.run_job."""

    @mock.patch("autils.devel.process.run")
    def test_run_job_invokes_lkp_run_with_ignore_status(self, run):
        """run_job never auto-answers and always ignores the exit status."""
        run.return_value = mock.Mock(exit_status=0)

        result = lkp.run_job(
            "/tmp/lkp-tests", "/tmp/lkp-tests/bench-1.yaml", timeout=60
        )

        self.assertIs(result, run.return_value)
        run.assert_called_once()
        cmd = run.call_args.args[0]
        self.assertIn("bin/lkp run", cmd)
        self.assertIn("/tmp/lkp-tests/bench-1.yaml", cmd)
        self.assertTrue(run.call_args.kwargs.get("ignore_status"))
        self.assertEqual(run.call_args.kwargs.get("timeout"), 60)


class FindResultFileTest(unittest.TestCase):
    """Unit tests for lkp.find_result_file."""

    @mock.patch("os.path.getmtime")
    @mock.patch("os.path.isfile")
    @mock.patch("glob.glob")
    @mock.patch("os.path.isdir")
    def test_returns_newest_match(self, isdir, glob_glob, isfile, getmtime):
        """The most recently modified match is returned."""
        isdir.return_value = True
        glob_glob.return_value = ["/root/a/mpstat.json", "/root/b/mpstat.json"]
        isfile.return_value = True
        getmtime.side_effect = lambda path: 1 if path.endswith("a/mpstat.json") else 2

        result = lkp.find_result_file("/root", "mpstat.json")

        self.assertEqual(result, "/root/b/mpstat.json")

    @mock.patch("os.path.isdir")
    def test_returns_none_when_no_match_found(self, isdir):
        """None is returned when root does not exist (or no file matches)."""
        isdir.return_value = False
        self.assertIsNone(lkp.find_result_file("/does/not/exist", "mpstat.json"))


class ArchiveResultsTest(unittest.TestCase):
    """Unit tests for lkp.archive_results."""

    @mock.patch("shutil.copytree")
    @mock.patch("os.path.islink")
    @mock.patch("os.path.exists")
    @mock.patch("os.path.isdir")
    @mock.patch("autils.devel.lkp.find_result_file")
    def test_archives_into_fresh_destination(
        self, find_result_file, isdir, exists, islink, copytree
    ):
        """The directory containing the newest result file is copied to dest."""
        find_result_file.return_value = "/tmp/lkp-tests/results/x/mpstat.json"
        isdir.return_value = False
        exists.return_value = False
        islink.return_value = False

        result = lkp.archive_results("/tmp/lkp-tests", "mpstat.json", "/dest")

        self.assertEqual(result, "/dest")
        copytree.assert_called_once_with("/tmp/lkp-tests/results/x", "/dest")

    @mock.patch("autils.devel.lkp.find_result_file")
    def test_returns_none_when_no_result_file(self, find_result_file):
        """archive_results gives up cleanly when nothing was found to archive."""
        find_result_file.return_value = None

        result = lkp.archive_results("/tmp/lkp-tests", "mpstat.json", "/dest")

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
