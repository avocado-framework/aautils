import os
import struct
import unittest
from unittest import TestCase, mock

from autils.devel import vfio


class VfioUtilsTests(TestCase):
    @mock.patch("os.open")
    def test_get_vfio_container_fd_fail(self, mock_open):
        mock_open.side_effect = OSError("No such file or directory")
        with self.assertRaises(ValueError) as e:
            vfio.get_vfio_container_fd()
        self.assertIn("Failed to open VFIO container", str(e.exception))

    @mock.patch("autils.devel.vfio.ioctl")
    def test_invalid_api_version(self, mock_ioctl):
        mock_ioctl.side_effect = OSError("Failed")
        with self.assertRaises(ValueError) as e:
            vfio.check_vfio_container(
                container_fd=3,
                vfio_get_api_version=15204,
                vfio_api_version=0,
                vfio_check_extension=15205,
                vfio_type_iommu=1,
            )
        self.assertIn("Failed to get API version", str(e.exception))

    @mock.patch("autils.devel.vfio.ioctl")
    def test_missing_iommu_extension(self, mock_ioctl):
        # API version matches; VFIO_CHECK_EXTENSION returns 0 when unsupported.
        mock_ioctl.side_effect = [0, 0]
        with self.assertRaises(ValueError) as e:
            vfio.check_vfio_container(
                container_fd=3,
                vfio_get_api_version=15204,
                vfio_api_version=0,
                vfio_check_extension=15205,
                vfio_type_iommu=1,
            )
        self.assertIn("does not support type 1 iommu", str(e.exception))

    @mock.patch("autils.devel.vfio.os.open")
    @mock.patch("autils.devel.vfio.pci.get_iommu_group")
    def test_get_group_fd_fail_1(self, mock_get_group, mock_open):
        mock_open.side_effect = OSError("No such file or directory")
        mock_get_group.return_value = "42"
        with self.assertRaises(ValueError) as e:
            vfio.get_iommu_group_fd(
                device="0000:03:00.0",
                vfio_group_get_status=100,
                vfio_group_flags_viable=0x1,
            )
        self.assertIn("Failed to open /dev/vfio/42", str(e.exception))

    @mock.patch("autils.devel.vfio.os.close")
    @mock.patch("autils.devel.vfio.ioctl")
    @mock.patch("autils.devel.vfio.os.open")
    @mock.patch("autils.devel.vfio.pci.get_iommu_group")
    def test_get_group_fd_fail_2(
        self, mock_get_group, mock_open, mock_ioctl, mock_close
    ):
        mock_open.return_value = 3
        mock_get_group.return_value = "42"
        argsz = struct.calcsize("II")
        # Pack request, ioctl returns same but with flags = 0
        mock_ioctl.return_value = struct.pack("II", argsz, 0)

        with self.assertRaises(ValueError) as ctx:
            vfio.get_iommu_group_fd(
                device="0000:03:00.0",
                vfio_group_get_status=100,
                vfio_group_flags_viable=0x1,
            )
        self.assertIn("Group not viable", str(ctx.exception))
        mock_close.assert_called_once_with(3)

    @mock.patch("autils.devel.vfio.os.close")
    @mock.patch("autils.devel.vfio.ioctl")
    @mock.patch("autils.devel.vfio.os.open")
    @mock.patch("autils.devel.vfio.pci.get_iommu_group")
    def test_get_group_fd_success(
        self, mock_get_group, mock_open, mock_ioctl, mock_close
    ):
        mock_open.return_value = 3
        mock_get_group.return_value = "42"
        argsz = struct.calcsize("II")
        # Pack request, ioctl returns same but with flags = 1
        mock_ioctl.return_value = struct.pack("II", argsz, 0x1)

        fd = vfio.get_iommu_group_fd(
            device="0000:03:00.0",
            vfio_group_get_status=100,
            vfio_group_flags_viable=0x1,
        )

        self.assertEqual(fd, 3)
        mock_open.assert_called_once_with("/dev/vfio/42", os.O_RDWR)
        mock_ioctl.assert_called_once()
        mock_close.assert_not_called()

    @mock.patch("autils.devel.vfio.ioctl")
    def test_attach_group_to_container_success(self, mock_ioctl):
        mock_ioctl.return_value = 0
        # Should not raise
        vfio.attach_group_to_container(
            group_fd=10, container_fd=20, vfio_group_set_container=100
        )
        mock_ioctl.assert_called_once_with(10, 100, struct.pack("i", 20))

    @mock.patch("autils.devel.vfio.ioctl")
    def test_attach_group_to_container_failure(self, mock_ioctl):
        mock_ioctl.side_effect = OSError("Failed")
        with self.assertRaises(ValueError) as ctx:
            vfio.attach_group_to_container(
                group_fd=10, container_fd=20, vfio_group_set_container=100
            )
        self.assertIn("failed to attach pci device", str(ctx.exception))

    @mock.patch("autils.devel.vfio.ioctl")
    def test_detach_group_from_container_success(self, mock_ioctl):
        mock_ioctl.return_value = 0
        vfio.detach_group_from_container(
            group_fd=10, container_fd=20, vfio_group_unset_container=200
        )
        mock_ioctl.assert_called_once_with(10, 200, struct.pack("i", 20))

    @mock.patch("autils.devel.vfio.ioctl")
    def test_detach_group_from_container_failure(self, mock_ioctl):
        mock_ioctl.side_effect = OSError("Failed")
        with self.assertRaises(ValueError) as ctx:
            vfio.detach_group_from_container(
                group_fd=10, container_fd=20, vfio_group_unset_container=200
            )
        self.assertIn("failed to detach pci device", str(ctx.exception))

    @mock.patch("autils.devel.vfio.ioctl")
    def test_get_device_fd_success(self, mock_ioctl):
        mock_ioctl.return_value = 50
        device_fd = vfio.get_device_fd(
            "0000:03:00.0", group_fd=10, vfio_group_get_device_fd=200
        )
        self.assertEqual(device_fd, 50)

    @mock.patch("autils.devel.vfio.ioctl")
    def test_get_device_fd_failure(self, mock_ioctl):
        mock_ioctl.side_effect = OSError("Device not found")
        with self.assertRaises(ValueError) as e:
            vfio.get_device_fd(
                "0000:03:00.0", group_fd=10, vfio_group_get_device_fd=200
            )
        self.assertIn("failed to get vfio device fd", str(e.exception))

    @mock.patch("autils.devel.vfio.ioctl")
    def test_vfio_device_supports_irq_success(self, mock_ioctl):
        argsz = struct.calcsize("IIII")
        response = struct.pack("IIII", argsz, 0, 0, 8)
        mock_ioctl.return_value = response

        result = vfio.vfio_device_supports_irq(
            device_fd=30,
            vfio_pci_msix_irq_index=100,
            vfio_device_get_irq_info=300,
            count=4,
        )
        self.assertTrue(result)

    @mock.patch("autils.devel.vfio.ioctl")
    def test_vfio_device_supports_irq_fail(self, mock_ioctl):
        argsz = struct.calcsize("IIII")
        response = struct.pack("IIII", argsz, 0, 0, 2)
        mock_ioctl.return_value = response

        result = vfio.vfio_device_supports_irq(
            device_fd=30,
            vfio_pci_msix_irq_index=100,
            vfio_device_get_irq_info=300,
            count=4,
        )
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
