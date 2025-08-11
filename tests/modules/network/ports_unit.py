import unittest.mock

from autils.network import ports


class PortTrackerTest(unittest.TestCase):
    def test_register_port(self):
        tracker = ports.PortTracker()
        ports.is_port_free = unittest.mock.MagicMock(return_value=True)
        self.assertNotIn(22, tracker.retained_ports)
        tracker.register_port(22)
        ports.is_port_free.assert_called_once_with(22, tracker.address)
        self.assertIn(22, tracker.retained_ports)

    def test_register_port_in_use(self):
        tracker = ports.PortTracker()
        ports.is_port_free = unittest.mock.MagicMock(return_value=False)
        with self.assertRaises(ValueError):
            tracker.register_port(22)
        ports.is_port_free.assert_called_once_with(22, tracker.address)

    def test_register_port_retained(self):
        tracker = ports.PortTracker()
        tracker.retained_ports = [22]
        with self.assertRaises(ValueError):
            tracker.register_port(22)

    def test_release_port(self):
        tracker = ports.PortTracker()
        tracker.retained_ports = [22]
        tracker.release_port(22)
        self.assertNotIn(22, tracker.retained_ports)

    def test_release_port_does_not_poke_system(self):
        tracker = ports.PortTracker()
        tracker.release_port = unittest.mock.MagicMock()
        ports.is_port_free = unittest.mock.MagicMock()
        tracker.release_port(22)
        tracker.release_port.assert_called_once_with(22)
        ports.is_port_free.assert_not_called()

    def test_find_free_port_from_tracker(self):
        tracker = ports.PortTracker()
        tracker._reset_retained_ports()
        ports.is_port_free = unittest.mock.MagicMock(return_value=True)
        port = tracker.find_free_port()
        self.assertEqual(port, tracker.start_port)
        self.assertIn(port, tracker.retained_ports)
        ports.is_port_free.assert_called_once_with(port, tracker.address)

    def test_find_free_port_from_tracker_with_start_port(self):
        tracker = ports.PortTracker()
        tracker._reset_retained_ports()
        ports.is_port_free = unittest.mock.MagicMock(return_value=True)
        port = tracker.find_free_port(start_port=6000)
        self.assertEqual(port, 6000)
        self.assertIn(port, tracker.retained_ports)

    def test_find_free_port_from_tracker_increments(self):
        tracker = ports.PortTracker()
        tracker._reset_retained_ports()
        tracker.retained_ports = [5000]
        ports.is_port_free = unittest.mock.MagicMock(return_value=True)
        port = tracker.find_free_port()
        self.assertEqual(port, 5001)
        self.assertIn(5001, tracker.retained_ports)
        ports.is_port_free.assert_called_with(5001, tracker.address)

    def test_borg_pattern(self):
        tracker1 = ports.PortTracker()
        tracker1._reset_retained_ports()
        tracker1.retained_ports.append(1234)
        tracker2 = ports.PortTracker()
        self.assertIn(1234, tracker2.retained_ports)
        tracker2.retained_ports.remove(1234)
        self.assertNotIn(1234, tracker1.retained_ports)
        tracker1._reset_retained_ports()

    def test_str(self):
        tracker = ports.PortTracker()
        tracker._reset_retained_ports()
        tracker.retained_ports.append(1234)
        self.assertEqual(str(tracker), "Ports tracked: [1234]")
        tracker._reset_retained_ports()


class PortsTest(unittest.TestCase):
    def test_is_port_available(self):
        port = ports.find_free_port(sequent=False)
        result = ports.is_port_available(port, "localhost")
        self.assertTrue(result)

    @unittest.mock.patch("socket.socket")
    def test_is_port_available_os_error(self, mock_socket):
        mock_socket.return_value.__enter__.return_value.bind.side_effect = OSError
        self.assertFalse(ports.is_port_available(22, "localhost"))

    @unittest.mock.patch("socket.socket")
    def test_is_port_available_permission_error(self, mock_socket):
        mock_socket.return_value.__enter__.return_value.bind.side_effect = (
            PermissionError
        )
        self.assertFalse(ports.is_port_available(22, "localhost"))

    def test_is_port_free_deprecation_warning(self):
        with self.assertWarns(DeprecationWarning):
            with unittest.mock.patch(
                "autils.network.ports.is_port_available"
            ) as mock_is_port_available:
                ports.is_port_free(22, "localhost")
                mock_is_port_available.assert_called_once_with(22, "localhost")

    def test_find_free_port(self):
        port = ports.find_free_port(sequent=False)
        self.assertEqual(type(port), int)

    @unittest.mock.patch("autils.network.ports.is_port_available")
    def test_find_free_port_not_found(self, mock_is_port_available):
        mock_is_port_available.return_value = False
        port = ports.find_free_port()
        self.assertIsNone(port)

    def test_find_free_ports(self):
        port = ports.find_free_ports(1000, 2000, 10)
        self.assertEqual(type(port), list)


if __name__ == "__main__":
    unittest.main()
