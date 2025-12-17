import logging
import os
import tempfile
import unittest


def setup_autils_loggers():
    """
    Setup autils loggers to contain at least one logger.

    This is required for tests that directly utilize autils modules
    because they require those loggers to be configured. Without this
    it might result in infinite recursion while attempting to log
    "No handlers could be found for logger ..." message.
    """
    for name in ("", "autils", "autils.test"):
        logger = logging.getLogger(name)
        if not logger.handlers:
            logger.handlers.append(logging.NullHandler())


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
        # pylint: disable=consider-using-with
        self.tmpdir = tempfile.TemporaryDirectory(prefix=prefix)

    def tearDown(self):
        self.tmpdir.cleanup()


def skipOnLevelsInferiorThan(level):
    """
    Skip tests based on the AUTILS_CHECK_LEVEL environment variable.

    This is useful for skipping long-running, resource-intensive,
    or time-sensitive tests during quick test runs.

    :param level: The minimum check level required to run the test
    :type level: int
    :return: unittest.skipIf decorator
    """
    return unittest.skipIf(
        int(os.environ.get("AUTILS_CHECK_LEVEL", 0)) < level,
        "Skipping test that take a long time to run, are "
        "resource intensive or time sensitive",
    )


def skipUnlessPathExists(path):
    """
    Skip tests unless a specific path exists on the system.

    :param path: The path to check for existence
    :type path: str
    :return: unittest.skipUnless decorator
    """
    return unittest.skipUnless(
        os.path.exists(path),
        (
            f'File or directory at path "{path}" '
            f"used in test is not available in the system"
        ),
    )
