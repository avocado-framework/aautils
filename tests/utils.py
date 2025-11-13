import os
import tempfile
import unittest


def temp_dir_prefix(klass):
    """
    Returns a standard name for the temp dir prefix used by the tests
    """
    return f"autils_{klass.__class__.__name__}_"


class TestCaseTmpDir(unittest.TestCase):
    """
    Base test case class that provides automatic temporary directory management.

    The temporary directory is created in setUp() and cleaned up in tearDown().
    Tests can access it via self.tmpdir.name
    """

    def setUp(self):
        prefix = temp_dir_prefix(self)
        self.tmpdir = tempfile.TemporaryDirectory(prefix=prefix)

    def tearDown(self):
        self.tmpdir.cleanup()


def skipUnlessPathExists(path):
    return unittest.skipUnless(
        os.path.exists(path),
        (
            f'File or directory at path "{path}" '
            f"used in test is not available in the system"
        ),
    )
