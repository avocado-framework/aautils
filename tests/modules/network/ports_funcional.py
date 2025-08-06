"""Functional tests for :pymod:`autils.network`.

These tests exercise a subset of the real functionality provided by the
``autils.network`` package, interacting with the underlying
operating system instead of relying solely on mocks.  Because some of the
operations may be slow or require resources that are not always available
(e.g. free TCP ports, the ``ip`` command).

Only a handful of simple and robust checks are implemented for each class
present in the corresponding unit test module.  The goal is to validate the
happy-path behaviour of the public API without interacting with privileged
resources or external hosts.
"""

import socket
import unittest

from autils.network import ports


class PortTrackerTest(unittest.TestCase):
    """Functional checks for :class:`autils.network.ports.PortTracker`."""

    def setUp(self) -> None:
        self.tracker = ports.PortTracker()
        self.tracker._reset_retained_ports()

    def test_register_and_release_port(self):
        """Find a free port, register it and then release it."""
        port = self.tracker.find_free_port()
        self.assertIsInstance(port, int)
        self.assertIn(port, self.tracker.retained_ports)

        # After releasing, the port must be no longer tracked and reported as
        # available.
        self.tracker.release_port(port)
        self.assertNotIn(port, self.tracker.retained_ports)
        self.assertTrue(ports.is_port_available(port, self.tracker.address))

    def test_borg_pattern_shares_state(self):
        """All tracker instances must share the *retained_ports* list."""
        port = self.tracker.find_free_port()

        second = ports.PortTracker()
        self.assertIn(port, second.retained_ports)


class PortsTest(unittest.TestCase):
    """Functional checks for utility helpers in *ports* module."""

    def test_find_free_port_and_bind(self):
        port = ports.find_free_port(sequent=False)
        self.assertIsInstance(port, int)

        # Ensure we can actually bind to the port that was reported as free.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("localhost", port))
            # If bind() succeeded, the port was indeed free.
            self.assertFalse(ports.is_port_available(port, "localhost"))

    def test_is_port_available_negative(self):
        # Let the OS choose a free port and bind to it, it must then be
        # reported as *not* available by the helper.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("localhost", 0))  # 0 => dynamically allocate
            bound_port = sock.getsockname()[1]
            self.assertFalse(ports.is_port_available(bound_port, "localhost"))


if __name__ == "__main__":
    unittest.main()
