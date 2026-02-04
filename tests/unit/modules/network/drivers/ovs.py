import unittest.mock

from autils.devel import process
from autils.network.drivers import ovs


class OvsTest(unittest.TestCase):
    @unittest.mock.patch("autils.network.drivers.ovs.process.run")
    def test_ovs_br_exists_true(self, mock_run):
        """Test ovs_br_exists when bridge exists"""
        mock_result = unittest.mock.MagicMock()
        mock_result.exit_status = 0
        mock_run.return_value = mock_result

        result = ovs.ovs_br_exists("br0")

        self.assertTrue(result)
        mock_run.assert_called_once_with("ovs-vsctl br-exists br0", shell=True)

    @unittest.mock.patch("autils.network.drivers.ovs.process.run")
    def test_ovs_br_exists_false(self, mock_run):
        """Test ovs_br_exists when bridge doesn't exist"""
        mock_result = unittest.mock.MagicMock()
        mock_result.exit_status = 1
        mock_run.return_value = mock_result

        result = ovs.ovs_br_exists("nonexistent")

        self.assertFalse(result)
        mock_run.assert_called_once_with("ovs-vsctl br-exists nonexistent", shell=True)

    @unittest.mock.patch("autils.network.drivers.ovs.process.run")
    def test_add_ovs_bridge(self, mock_run):
        """Test add_ovs_bridge function"""
        bridge_name = "test-bridge"

        ovs.add_ovs_bridge(bridge_name)

        expected_cmd = f"ovs-vsctl --may-exist add-br {bridge_name}"
        mock_run.assert_called_once_with(expected_cmd, shell=True)

    @unittest.mock.patch("autils.network.drivers.ovs.process.run")
    def test_del_ovs_bridge(self, mock_run):
        """Test del_ovs_bridge function"""
        bridge_name = "test-bridge"

        ovs.del_ovs_bridge(bridge_name)

        expected_cmd = f"ovs-vsctl --if-exists del-br {bridge_name}"
        mock_run.assert_called_once_with(expected_cmd, shell=True)

    @unittest.mock.patch("autils.network.drivers.ovs.process.run")
    def test_ovs_br_exists_cmd_error(self, mock_run):
        """Test ovs_br_exists when process.run raises CmdError"""
        mock_run.side_effect = process.CmdError("ovs-vsctl br-exists test", None, "Command failed")

        with self.assertRaises(process.CmdError):
            ovs.ovs_br_exists("test")

    @unittest.mock.patch("autils.network.drivers.ovs.process.run")
    def test_add_ovs_bridge_cmd_error(self, mock_run):
        """Test add_ovs_bridge when process.run raises CmdError"""
        mock_run.side_effect = process.CmdError("ovs-vsctl --may-exist add-br test", None, "Command failed")

        with self.assertRaises(process.CmdError):
            ovs.add_ovs_bridge("test")

    @unittest.mock.patch("autils.network.drivers.ovs.process.run")
    def test_del_ovs_bridge_cmd_error(self, mock_run):
        """Test del_ovs_bridge when process.run raises CmdError"""
        mock_run.side_effect = process.CmdError("ovs-vsctl --if-exists del-br test", None, "Command failed")

        with self.assertRaises(process.CmdError):
            ovs.del_ovs_bridge("test")


if __name__ == "__main__":
    unittest.main()
