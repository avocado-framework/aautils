import unittest

from autils.devel import process
from autils.file import path
from autils.network.drivers import ovs


def ovs_vsctl_available():
    """Check if ovs-vsctl command is available."""
    try:
        path.find_command("ovs-vsctl")
        return True
    except path.CmdNotFoundError:
        return False


def can_run_ovs_commands():
    """Check if we can run basic ovs-vsctl commands."""
    if not ovs_vsctl_available():
        return False
    try:
        # Try a simple read-only command that should work without root
        process.run("ovs-vsctl show", ignore_status=True, shell=True)
        return True
    except process.CmdError:
        return False


class OvsTest(unittest.TestCase):
    """Functional checks for :mod:`autils.network.drivers.ovs`."""

    def setUp(self):
        """Set up test bridge name that's unlikely to conflict."""
        self.test_bridge = "test-br-aautils-func"
        # Clean up any leftover bridge from previous runs
        if ovs_vsctl_available() and can_run_ovs_commands():
            try:
                ovs.del_ovs_bridge(self.test_bridge)
            except process.CmdError:
                pass  # Bridge may not exist, which is fine

    def tearDown(self):
        """Clean up test bridge."""
        if ovs_vsctl_available() and can_run_ovs_commands():
            try:
                ovs.del_ovs_bridge(self.test_bridge)
            except process.CmdError:
                pass  # Best effort cleanup

    @unittest.skipUnless(ovs_vsctl_available(), "ovs-vsctl command not available")
    @unittest.skipUnless(can_run_ovs_commands(), "Cannot run ovs-vsctl commands")
    def test_ovs_br_exists_false_for_nonexistent_bridge(self):
        """Test ovs_br_exists returns False for non-existent bridge."""
        # Ensure bridge doesn't exist
        self.assertFalse(ovs.ovs_br_exists("nonexistent-bridge-12345"))

    @unittest.skipUnless(ovs_vsctl_available(), "ovs-vsctl command not available")
    @unittest.skipUnless(can_run_ovs_commands(), "Cannot run ovs-vsctl commands")
    def test_bridge_lifecycle(self):
        """Test complete bridge lifecycle: add, exists, delete."""
        # Initially, bridge should not exist
        self.assertFalse(ovs.ovs_br_exists(self.test_bridge))

        # Add the bridge
        try:
            ovs.add_ovs_bridge(self.test_bridge)
            # Bridge should now exist
            self.assertTrue(ovs.ovs_br_exists(self.test_bridge))
        except process.CmdError as e:
            # If we can't create bridges (e.g., no root access or OVS daemon not running)
            self.skipTest(f"Cannot create OVS bridge: {e}")

        # Delete the bridge
        ovs.del_ovs_bridge(self.test_bridge)
        # Bridge should no longer exist
        self.assertFalse(ovs.ovs_br_exists(self.test_bridge))

    def test_functions_without_ovs_installed(self):
        """Test that functions fail appropriately when OVS is not available."""
        if ovs_vsctl_available():
            self.skipTest("OVS is available, skipping unavailability test")

        # When ovs-vsctl is not available, functions should raise appropriate errors
        with self.assertRaises((process.CmdError, FileNotFoundError, OSError)):
            ovs.ovs_br_exists("test")

        with self.assertRaises((process.CmdError, FileNotFoundError, OSError)):
            ovs.add_ovs_bridge("test")

        with self.assertRaises((process.CmdError, FileNotFoundError, OSError)):
            ovs.del_ovs_bridge("test")


if __name__ == "__main__":
    unittest.main()
