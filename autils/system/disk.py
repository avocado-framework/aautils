# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
#
# See LICENSE for more details.
#
# This code was inspired in the autotest project,
#
# client/base_utils.py
#
# Copyright: 2018 IBM
# Authors : Praveen K Pandey <praveen@linux.vnet.ibm.com>
#         : Narasimhan V <sim@linux.vnet.ibm.com>
#         : Naresh Bannoth <nbannoth@linux.vnet.ibm.com>
#         : Maram Srimannarayana Murthy <msmurthy@linux.vnet.ibm.com>


"""Disk utilities.

This module provides comprehensive disk management utilities including:

- Basic disk operations (create, delete, partition, mount)
- Disk information retrieval (size, filesystem, mount points)
- Advanced cleanup operations (LVM, RAID, multipath)
- Device-agnostic abstractions for NVMe, SCSI, IDE, virtio, etc.
"""

# pylint: disable=too-many-lines


import glob
import json
import logging
import os
import re
import time

from avocado.utils import multipath

from autils.devel import process
from autils.devel.wait import wait_for
from autils.file import genio

LOGGER = logging.getLogger(__name__)

# Retry and timeout constants
MAX_UNMOUNT_RETRIES = 3
UNMOUNT_RETRY_DELAY_SECONDS = 10
RAID_STOP_RETRIES = 5
RAID_STOP_TIMEOUT_SECONDS = 5
WIPE_RETRY_ATTEMPTS = 5
WIPE_RETRY_DELAY_SECONDS = 2

# Disk operation constants
DEFAULT_WIPE_SIZE_MB = 100
PARTITION_TABLE_ZERO_BLOCKS = 1
METADATA_ZERO_BLOCKS = 2048
METADATA_ZERO_BLOCK_SIZE = 512

# System settle timeouts
UDEV_SETTLE_TIMEOUT_SECONDS = 15
DEVICE_STABILIZATION_DELAY_SECONDS = 5
RAID_STOP_DELAY_SECONDS = 2
DM_SUSPEND_DELAY_SECONDS = 1
UMOUNT_FORCE_RETRY_COUNT = 5
UMOUNT_FORCE_RETRY_DELAY = 0.3

# Valid modes accepted by cleanup_disks()
CLEANUP_DISK_VALID_MODES = frozenset({"auto", "light", "full"})


class DiskError(Exception):
    """Generic DiskError."""


def freespace(path):
    """Return available free space for a path.

    :param path: filesystem path to inspect
    :type path: str

    :return: free space in bytes
    :rtype: int
    """
    fs_stats = os.statvfs(path)
    return fs_stats.f_bsize * fs_stats.f_bavail


def get_disk_blocksize(path):
    """Return the disk block size.

    :param path: filesystem path to inspect
    :type path: str

    :return: disk block size, in bytes
    :rtype: int
    """
    fs_stats = os.statvfs(path)
    return fs_stats.f_bsize


def create_loop_device(size, blocksize=4096, directory="./"):
    """Create a loop device of the specified size and blocksize.

    :param size: Size of loop device, in bytes
    :type size: int
    :param blocksize: block size of loop device, in bytes. Defaults to 4096
    :type blocksize: int
    :param directory: Directory where the backing file will be created.
                      Defaults to current directory.
    :type directory: str

    :return: loop device name
    :rtype: str
    :raises DiskError: if the backing file or loop device cannot be created
    """
    cmd = "losetup --find"
    loop = process.run(cmd, ignore_status=True, sudo=True).stdout_text.strip("\n")

    loop_file = os.path.join(directory, f"tmp_{loop.split('/')[-1]}.img")
    cmd = (
        f"dd if=/dev/zero of={loop_file} bs={blocksize} "
        f"count={int(size / blocksize)}"
    )
    if process.system(cmd, ignore_status=True, sudo=True):
        raise DiskError("Unable to create backing file for loop device")

    cmd = f"losetup {loop} {loop_file} -P"
    if process.system(cmd, ignore_status=True, sudo=True):
        raise DiskError("Unable to create the loop device")
    return loop


def delete_loop_device(device):
    """Delete the specified loop device.

    :param device: device to be deleted
    :type device: str

    :return: True if deleted.
    :rtype: bool
    :raises DiskError: if the loop device cannot be found or deleted
    """
    cmd = "losetup -aJl"
    loop_dic = json.loads(process.run(cmd, ignore_status=True, sudo=True).stdout_text)
    loop_file = ""
    for loop_dev in loop_dic["loopdevices"]:
        if device == loop_dev["name"]:
            loop_file = loop_dev["back-file"]
    if not loop_file:
        raise DiskError("Unable to find backing file for loop device")
    cmd = f"losetup -d {device}"
    if process.system(cmd, ignore_status=True, sudo=True):
        raise DiskError("Unable to delete the loop device")
    os.remove(loop_file)
    return True


def get_disks():
    """Return the physical "hard drives" available on this system.

    This is a simple wrapper around `lsblk` and will return all the
    top level physical (non-virtual) devices return by it.

    TODO: this is currently Linux specific.  Support for other
    platforms is desirable and may be implemented in the future.

    :returns: a list of paths to the physical disks on the system
    :rtype: list[str]
    :raises DiskError: if disk discovery or JSON parsing fails
    """
    try:
        json_result = process.run("lsblk --json --paths --inverse")
    except process.CmdError as ce:
        raise DiskError(f"Error occurred while executing lsblk command: {ce}") from ce
    try:
        json_data = json.loads(json_result.stdout_text)
    except json.JSONDecodeError as je:
        raise DiskError(f"Error occurred while parsing JSON data: {je}") from je
    disks = []
    to_process = json_data.get("blockdevices", [])
    for device in to_process:
        disks.append(device.get("name"))
        if "children" in device:
            to_process.extend(device["children"])
    return disks


def get_all_disk_paths():
    """Return all available disk names and aliases on this system.

    This will get all the sysfs disks name entries by its device
    node name, by-uuid, by-id and by-path, irrespective of any
    platform and device type

    :returns: a list of all disk path names
    :rtype: list[str]
    """
    disk_list = []
    for path in [
        "/dev",
        "/dev/mapper",
        "/dev/disk/by-id",
        "/dev/disk/by-path",
        "/dev/disk/by-uuid",
        "/dev/disk/by-partuuid",
        "/dev/disk/by-partlabel",
    ]:
        if os.path.exists(path):
            abs_path = []
            for device in os.listdir(path):
                abs_path.append(os.path.join(path, device))
            disk_list.extend(abs_path)
    return disk_list


def get_absolute_disk_path(device):
    """Return absolute device path of given disk.

    This will get actual disks path of given device, it can take
    node name, by-uuid, by-id and by-path, irrespective of any
    platform and device type

    :param device: disk name or disk alias names sda or scsi-xxx
    :type device: str

    :returns: the device absolute path name
    :rtype: str
    """
    if not os.path.exists(device):
        for dev_path in get_all_disk_paths():
            if device == os.path.basename(dev_path):
                return dev_path
    return device


def get_available_filesystems():
    """Return a list of all available filesystem types.

    :returns: a list of filesystem types
    :rtype: list[str]
    """
    filesystems = set()
    with open("/proc/filesystems") as proc_fs:  # pylint: disable=W1514
        for proc_fs_line in proc_fs.readlines():
            filesystems.add(re.sub(r"(nodev)?\s*", "", proc_fs_line))
    return list(filesystems)


def get_filesystem_type(mount_point="/"):
    """Return the filesystem type for the informed mount point.

    The default mount point considered when none is informed
    is the root "/" mount point.

    :param str mount_point: mount point to asses the filesystem type.
                            Default "/"

    :returns: filesystem type
    :rtype: str
    """
    with open("/proc/mounts") as mounts:  # pylint: disable=W1514
        for mount_line in mounts.readlines():
            _, fs_file, fs_vfstype, _, _, _ = mount_line.split()
            if fs_file == mount_point:
                return fs_vfstype
    return None


def is_root_device(device):
    """Check for root disk.

    :param device: device to check
    :type device: str

    :returns: True or False, True if given device is root disk
              otherwise will return False.
    :rtype: bool
    """
    cmd = "lsblk --j -o MOUNTPOINT,PKNAME"
    output = process.run(cmd)
    result = json.loads(output.stdout_text)
    for item in result["blockdevices"]:
        if item["mountpoint"] == "/" and device == str(item["pkname"]):
            return True
    return False


def is_disk_mounted(device):
    """Check if given disk is mounted or not.

    :param device: disk/device name
    :type device: str

    :returns: True if the device/disk is mounted else False
    :rtype: bool
    """
    with open("/proc/mounts") as mounts:  # pylint: disable=W1514
        for mount_line in mounts.readlines():
            dev, _, _, _, _, _ = mount_line.split()
            if dev == device:
                return True
        return False


def is_dir_mounted(dir_path):
    """Check if given directory is mounted or not.

    :param dir_path: directory path
    :type dir_path: str

    :returns: True if the given directory is mounted else False
    :rtype: bool
    """
    with open("/proc/mounts") as mounts:  # pylint: disable=W1514
        for mount_line in mounts.readlines():
            _, fs_dir, _, _, _, _ = mount_line.split()
            if fs_dir == dir_path:
                return True
        return False


def fs_exists(device):
    """Check if filesystem exists on given disk/device.

    :param device: disk/device name
    :type device: str

    :returns: returns True if filesystem exists on the given disk else False
    :rtype: bool
    """
    cmd = f"blkid -o value -s TYPE {device}"
    out = process.system_output(cmd, shell=True, ignore_status=True).decode("utf-8")
    fs_list = ["ext2", "ext3", "ext4", "xfs", "btrfs"]
    if out in fs_list:
        return True
    return False


def get_dir_mountpoint(dir_path):
    """Get mounted disk name that is mounted on given dir_path.

    :param dir_path: absolute directory path
    :type dir_path: str

    :returns: returns disk name which mounted on given dir_path
    :rtype: str
    """
    with open("/proc/mounts") as mounts:  # pylint: disable=W1514
        for mount_line in mounts.readlines():
            dev, fs_dir, _, _, _, _ = mount_line.split()
            if fs_dir == dir_path:
                return dev
        return None


def get_disk_mountpoint(device):
    """Get mountpoint on which given disk is mounted.

    :param device: disk/device name
    :type device: str

    :return: return directory name on which disk is mounted
    :rtype: str
    """
    with open("/proc/mounts") as mounts:  # pylint: disable=W1514
        for mount_line in mounts.readlines():
            dev, fs_dir, _, _, _, _ = mount_line.split()
            if dev == device:
                return fs_dir
        return None


def create_linux_raw_partition(disk_name, size=None, num_of_par=1):
    """Create partitions using sfdisk command.

    :param disk_name: disk/device name
    :type disk_name: str
    :param size: size of partition
    :type size: str
    :param num_of_par: Number of partitions to be created
    :type num_of_par: int

    :return: list of created partitions, or None if partitioning did not change
    :rtype: list[str] or None
    :raises DiskError: if creating partitions fails
    """
    if not size:
        size = get_size_of_disk(disk_name) / 1073741824
        size = size / num_of_par
        size = str(size) + "G"
    partitions = [
        "size= +" + size if val != 3 else "type=5" for val in range(0, num_of_par + 1)
    ]
    disk_partition_file = (
        "/tmp/creat_partition" + process.run("date '+%d-%m-%y_%T'").stdout_text.strip()
    )
    if not os.path.isfile(disk_partition_file):
        process.run("touch " + disk_partition_file)
    for line in partitions:
        genio.append_one_line(disk_partition_file, line)
    try:
        part_output = process.getoutput(
            "sfdisk " + disk_name + " < " + disk_partition_file
        )
    except Exception as exc:
        msg = f"sfdisk partition creation command failed on disk {disk_name}"
        LOGGER.warning(msg)
        raise DiskError(msg) from exc
    rescan_disk(disk_name)
    if "The partition table has been altered" in part_output:
        return get_disk_partitions(disk_name)
    return None


def get_size_of_disk(disk):
    """Return size of disk in bytes.

    :param disk: disk/device name
    :type disk: str

    :return: size of disk in bytes
    :rtype: int
    """
    return int(process.getoutput("lsblk -b --output SIZE -n -d " + disk))


def delete_partition(partition_name):
    """Delete mentioned partition from disk.

    :param partition_name: partition absolute path
    :type partition_name: str

    :raises DiskError: if deleting the partition fails
    """
    disk_index = re.search(r"\d+", partition_name).start()
    try:
        process.run(
            "sfdisk --delete "
            + partition_name[:disk_index]
            + " "
            + partition_name[disk_index:]
        )
    except Exception as exc:
        msg = f"sfdisk --delete command failed on disk {partition_name}"
        LOGGER.warning(msg)
        raise DiskError(msg) from exc


def clean_disk(disk_name):
    """Clean partitions table of a disk.

    :param disk_name: disk name
    :type disk_name: str
    """
    output = process.getoutput("sfdisk --delete " + disk_name)
    rescan_disk(disk_name)
    if not get_disk_partitions(disk_name):
        if "The partition table has been altered" in output:
            process.run("wipefs -af " + disk_name)


def rescan_disk(disk_name):
    """Re-scan disk.

    :param disk_name: disk name
    :type disk_name: str
    """
    disk_name = os.path.realpath(disk_name)
    if re.search(r"dm-\d+", disk_name):
        mpath_dict = multipath.get_multipath_details()
        for _ in range(len(mpath_dict["maps"])):
            if mpath_dict["maps"][_]["sysfs"] == disk_name.split("/")[-1]:
                disk_name = (
                    "/dev/" + mpath_dict["maps"][_]["path_groups"][0]["paths"][0]["dev"]
                )
                break
    process.run(f"echo 1 > /sys/block/{disk_name}/device/rescan")


def get_disk_partitions(disk):
    """Return partitions of a disk excluding extended partition.

    :param disk: disk name
    :type disk: str

    :return: all non-extended partitions of disk
    :rtype: list[str]
    """
    rescan_disk(disk)
    partitions_op = process.getoutput("sfdisk -l " + disk)
    return [
        line.split(" ")[0]
        for line in partitions_op.split("\n")
        if line.startswith(disk) and "Extended" not in line
    ]


def get_io_scheduler_list(device_name):
    """Return IO schedulers available for the IO device.

    :param device_name: Device  name example like sda
    :type device_name: str

    :return: list of IO scheduler
    :rtype: list[str]
    """
    with open(__sched_path(device_name), "r", encoding="utf-8") as fl:
        return fl.read().translate(str.maketrans("[]", " ")).split()


def get_io_scheduler(device_name):
    """Return the IO scheduler currently set for device.

    :param device_name: Device  name example like sda
    :type device_name: str

    :return: IO scheduler
    :rtype: str
    """
    return re.split(
        r"[\[\]]", open(__sched_path(device_name), "r", encoding="utf-8").read()
    )[1]


def __sched_path(device_name):

    file_path = f"/sys/block/{device_name}/queue/scheduler"
    return file_path


def set_io_scheduler(device_name, name):
    """Set IO scheduler to a device.

    :param device_name:  Device  name example like sda
    :type device_name: str
    :param name: io scheduler name
    :type name: str

    :raises DiskError: if the IO scheduler is not available for the device
    """
    if name not in get_io_scheduler_list(device_name):
        raise DiskError(f"No such IO scheduler: {name}")

    with open(__sched_path(device_name), "w", encoding="utf-8") as fp:
        fp.write(name)


def normalize_multipath_devices(devices, logger=None):
    """Map raw devices to multipath devices if available.

    Works with FC, iSCSI, SAS multipath configurations.

    :param devices: list of device names
    :type devices: list[str]
    :param logger: logger instance
    :type logger: logging.Logger or None

    :return: list of normalized device names
    :rtype: list[str]
    """
    log = logger or LOGGER
    normalized = []
    seen = set()

    for dev in devices:
        dev_name = dev.replace("/dev/", "")
        holder_path = f"/sys/block/{dev_name}/holders"

        try:
            if not os.path.exists(holder_path):
                normalized.append(dev_name)
                continue

            holders = os.listdir(holder_path)
            if not holders:
                normalized.append(dev_name)
                continue
        except (OSError, PermissionError) as e:
            log.debug("Cannot access holders for %s: %s", dev_name, e)
            normalized.append(dev_name)
            continue

        found = False
        for holder in holders:
            if holder.startswith("dm-"):
                try:
                    mpath = multipath.get_mpath_from_dm(holder)
                    if mpath and mpath not in seen:
                        # Use /dev/mapper/<name> for device operations (correct
                        # block device path) and keep the dm-N name in `seen`
                        # so sysfs lookups using dm-N remain available to
                        # downstream discovery helpers.
                        mapper_path = f"/dev/mapper/{mpath}"
                        log.info("  %s → %s (dm: %s)", dev_name, mapper_path, holder)
                        normalized.append(mapper_path)
                        seen.add(mpath)
                        seen.add(holder)
                        found = True
                        break
                except (OSError, ValueError, KeyError) as e:
                    log.debug("Failed to get mpath for %s: %s", holder, e)

        if not found:
            normalized.append(dev_name)

    return normalized


def _find_partitions(devices):
    """Find partitions belonging to the given devices.

    :param devices: list of device names (bare names, e.g. ``sda``)
    :type devices: list[str]

    :return: list of partition names found under each device in sysfs
    :rtype: list[str]
    """
    parts = []
    for dev in devices:
        dev_path = f"/sys/block/{dev}"
        try:
            if not os.path.exists(dev_path):
                continue

            entries = os.listdir(dev_path)
            for entry in entries:
                if not entry.startswith(dev):
                    continue
                entry_path = os.path.join(dev_path, entry)
                partition_file = os.path.join(entry_path, "partition")
                if os.path.isdir(entry_path) and os.path.exists(partition_file):
                    parts.append(entry)
        except (OSError, PermissionError):
            # Skip devices we cannot access
            continue
    return parts


def _find_lvm_structures(all_devs, logger):  # pylint: disable=too-many-nested-blocks
    """Find LVM volume groups, logical volumes, and physical volumes.

    :param all_devs: list of device names to inspect
    :type all_devs: list[str]
    :param logger: logger instance
    :type logger: logging.Logger or None

    :return: tuple of (vgs set, lvs list of (vg, lv) tuples, pvs set)
    :rtype: tuple[set, list, set]
    """
    log = logger or LOGGER
    vgs = set()
    lvs = []
    pvs = set()

    for dev in all_devs:
        cmd = f"pvs --noheadings -o vg_name /dev/{dev} 2>/dev/null"
        result = process.run(cmd, ignore_status=True, shell=True, sudo=True)
        if not result.exit_status and result.stdout_text.strip():
            for line in result.stdout_text.splitlines():
                vg = line.strip()
                if vg:
                    vgs.add(vg)

    for vg in list(vgs):  # pylint: disable=too-many-nested-blocks
        try:
            # Check VG existence via vgs command (no lv_utils dependency)
            check = process.run(
                f"vgs {vg} 2>/dev/null", ignore_status=True, shell=True, sudo=True
            )
            if not check.exit_status:
                cmd = f"lvs --noheadings -o lv_name {vg} 2>/dev/null"
                result = process.run(cmd, ignore_status=True, shell=True, sudo=True)
                if not result.exit_status:
                    lvs.extend(
                        [
                            (vg, lv.strip())
                            for lv in result.stdout_text.strip().split("\n")
                            if lv.strip()
                        ]
                    )

                cmd = f"pvs --noheadings -o pv_name {vg} 2>/dev/null"
                result = process.run(cmd, ignore_status=True, shell=True, sudo=True)
                if not result.exit_status and result.stdout_text.strip():
                    for pv in result.stdout_text.strip().split("\n"):
                        pv = pv.strip()
                        if pv:
                            pv = pv.replace("/dev/", "")
                            pvs.add(pv)
                            log.debug("  Tracked PV %s from VG %s", pv, vg)
        except (OSError, ValueError) as e:
            log.debug("Failed to process VG %s: %s", vg, e)

    return vgs, lvs, pvs


def _find_raid_arrays(  # pylint: disable=too-many-branches,too-many-locals,too-many-nested-blocks
    all_devs,
):
    """Find RAID arrays that contain any of the given devices.

    :param all_devs: list of device names to inspect
    :type all_devs: list[str]

    :return: set of RAID device paths (e.g. ``/dev/md0``, ``/dev/md/myarray``)
    :rtype: set[str]
    """
    raids = set()

    for dev in all_devs:
        if os.path.exists(f"/dev/{dev}"):
            cmd = f"mdadm --examine /dev/{dev} 2>/dev/null"
            result = process.run(cmd, ignore_status=True, shell=True, sudo=True)
            if not result.exit_status and "Magic" in result.stdout_text:
                for line in result.stdout_text.split("\n"):
                    if "Name :" in line:
                        raid_name = line.split(":", 1)[1].strip()
                        named_path = f"/dev/md/{raid_name}"
                        if os.path.exists(named_path):
                            raids.add(named_path)
                        break

    if os.path.exists("/proc/mdstat"):
        try:
            with open("/proc/mdstat", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("md"):
                        md_dev = line.split()[0]
                        slaves_path = f"/sys/block/{md_dev}/slaves"
                        try:
                            if not os.path.exists(slaves_path):
                                continue
                            slaves = os.listdir(slaves_path)
                        except (OSError, PermissionError):
                            continue

                        for d in all_devs:
                            real_d = os.path.basename(
                                os.path.realpath(f"/dev/{d}")
                            )
                            if d in slaves or real_d in slaves:
                                raids.add(f"/dev/{md_dev}")
                                for named_link in glob.glob("/dev/md/*"):
                                    try:
                                        real = os.path.realpath(named_link)
                                        if real == f"/dev/{md_dev}":
                                            raids.add(named_link)
                                    except OSError:
                                        pass
                                break
        except (IOError, OSError):
            pass

    return raids


def _check_raid_for_lvm(raids, existing_lvs):  # pylint: disable=too-many-nested-blocks
    """Check RAID devices for LVM structures stacked on top.

    :param raids: iterable of RAID device paths
    :type raids: iterable[str]
    :param existing_lvs: already-discovered list of (vg, lv) tuples to avoid
                         duplicates
    :type existing_lvs: list[tuple[str, str]]

    :return: tuple of (vgs set, lvs list of (vg, lv) tuples)
    :rtype: tuple[set, list]
    """
    vgs = set()
    lvs = []

    for raid_dev in raids:  # pylint: disable=too-many-nested-blocks
        cmd = f"pvs --noheadings -o vg_name {raid_dev} 2>/dev/null"
        result = process.run(cmd, ignore_status=True, shell=True, sudo=True)
        if not result.exit_status and result.stdout_text.strip():
            for line in result.stdout_text.splitlines():
                vg = line.strip()
                if not vg:
                    continue
                vgs.add(vg)
                try:
                    check = process.run(
                        f"vgs {vg} 2>/dev/null",
                        ignore_status=True,
                        shell=True,
                        sudo=True,
                    )
                    if not check.exit_status:
                        cmd2 = f"lvs --noheadings -o lv_name {vg} 2>/dev/null"
                        result2 = process.run(
                            cmd2, ignore_status=True, shell=True, sudo=True
                        )
                        if not result2.exit_status:
                            for raw_lv in result2.stdout_text.strip().split("\n"):
                                lv = raw_lv.strip()
                                if not lv:
                                    continue
                                entry = (vg, lv)
                                if entry in existing_lvs or entry in lvs:
                                    continue
                                lvs.append(entry)
                except (OSError, ValueError) as e:
                    LOGGER.debug(
                        "Failed to check RAID %s for LVM: %s", raid_dev, e
                    )

    return vgs, lvs


def _build_mount_points(devices, parts, raids, lvs):
    """Build a comprehensive list of mount point candidates.

    :param devices: list of base device names
    :type devices: list[str]
    :param parts: list of partition names
    :type parts: list[str]
    :param raids: collection of RAID device paths
    :type raids: iterable[str]
    :param lvs: list of (vg_name, lv_name) tuples
    :type lvs: list[tuple[str, str]]

    :return: tuple of (mounts list, all_devs list)
    :rtype: tuple[list[str], list[str]]
    """
    lv_devs = [f"{vg}-{lv}" for vg, lv in lvs]
    all_devs = list(set(devices + parts + lv_devs + list(raids)))

    mounts = []
    for dev in all_devs:
        mounts.extend([dev, f"/dev/{dev}", f"/dev/mapper/{dev}"])
    for md in raids:
        mounts.extend([f"/dev/md/{md.replace('md', '')}", f"/dev/{md}"])
    for vg, lv in lvs:
        mounts.extend([f"/dev/{vg}/{lv}", f"/dev/mapper/{vg}-{lv}"])

    return list(set(mounts)), all_devs


def build_device_dependencies(devices, logger=None):
    """Build dependency graph for storage devices.

    Discovers partitions, LVM, RAID, mounts, and swap.
    Device-type agnostic (NVMe, SCSI, IDE, virtio, multipath).

    :param devices: list of device names
    :type devices: list[str]
    :param logger: logger instance
    :type logger: logging.Logger or None

    :return: dict with keys ``parts``, ``vgs``, ``lvs``, ``raids``,
             ``devs``, ``mounts``, ``pvs``
    :rtype: dict
    """
    log = logger or LOGGER

    parts = _find_partitions(devices)
    all_devs = devices + parts

    vgs, lvs, pvs = _find_lvm_structures(all_devs, log)
    raids = _find_raid_arrays(all_devs)

    raid_vgs, raid_lvs = _check_raid_for_lvm(sorted(raids), lvs)
    vgs.update(raid_vgs)
    lvs.extend(raid_lvs)

    mounts, final_devs = _build_mount_points(devices, parts, raids, lvs)

    return {
        "parts": parts,
        "vgs": list(vgs),
        "lvs": lvs,
        "raids": list(raids),
        "devs": final_devs,
        "mounts": mounts,
        "pvs": list(pvs),
    }


def unmount_devices(  # pylint: disable=too-many-branches,too-many-statements,too-many-locals,too-many-nested-blocks
    devices,
    logger=None,
    is_swap=False,
    max_retries=MAX_UNMOUNT_RETRIES,
    retry_delay=UNMOUNT_RETRY_DELAY_SECONDS,
):
    """Unmount filesystems or disable swap with retry logic.

    Works with NVMe, SCSI, IDE, virtio, multipath devices.

    :param devices: list of device names or mount points
    :type devices: list[str]
    :param logger: logger instance
    :type logger: logging.Logger or None
    :param is_swap: True for swap, False for filesystems
    :type is_swap: bool
    :param max_retries: maximum retry attempts
    :type max_retries: int
    :param retry_delay: delay between retries in seconds
    :type retry_delay: int

    :return: list of error messages
    :rtype: list[str]
    """
    log = logger or LOGGER
    errors = []
    proc_file = "/proc/swaps" if is_swap else "/proc/mounts"

    for attempt in range(max_retries):
        try:
            with open(proc_file, "r", encoding="utf-8") as f:
                lines = f.readlines()[1:] if is_swap else f.readlines()
        except (IOError, OSError, PermissionError) as e:
            return [f"Failed to read {proc_file}: {e}"]

        done = set()
        count = 0
        remaining = []

        for line in lines:
            parts = line.split()
            if len(parts) < 2:
                continue

            dev = parts[0]
            mnt = parts[1] if not is_swap else parts[0]

            if mnt in done:
                continue

            match = False
            for d in devices:
                try:
                    real_d = os.path.basename(os.path.realpath(f"/dev/{d}"))
                    real_dev = os.path.basename(os.path.realpath(dev))
                except OSError:
                    real_d = os.path.basename(d)
                    real_dev = os.path.basename(dev)
                if real_dev == real_d:
                    match = True
                    break
                if real_dev.startswith(real_d) and real_d:
                    remainder = real_dev[len(real_d):]
                    if remainder:
                        if real_d[-1].isdigit():
                            if remainder.startswith("p") and remainder[1:].isdigit():
                                match = True
                                break
                        elif remainder.isdigit():
                            match = True
                            break

            if match:
                action = "swapoff" if is_swap else "umount"
                log.info("  %sing %s from %s", action.title(), dev, mnt)
                cmd = f"swapoff {dev}" if is_swap else f"umount -f -l {mnt}"
                result = process.run(cmd, ignore_status=True, shell=True, sudo=True)
                if result.exit_status:
                    remaining.append((dev, mnt, result.stderr_text))
                else:
                    done.add(mnt)
                    count += 1

        if count > 0:
            log.info("  Processed %d %s(s)", count, "swap" if is_swap else "mount")
            if not is_swap:
                process.run(
                    "udevadm settle --timeout=2",
                    ignore_status=True,
                    shell=True,
                    sudo=True,
                )

        if not remaining:
            if not count and not attempt:
                log.info("  No %s found", "swap" if is_swap else "mounts")
            return errors

        if attempt < max_retries - 1:
            log.info(
                "  %d %s(s) failed, retrying in %ss (attempt %d/%d)",
                len(remaining),
                "swap" if is_swap else "mount",
                retry_delay,
                attempt + 1,
                max_retries,
            )
            time.sleep(retry_delay)
        else:
            for dev, mnt, err in remaining:
                action = "swapoff" if is_swap else "umount"
                errors.append(
                    f"Failed {action} {dev} from {mnt} after "
                    f"{max_retries} attempts: {err}"
                )

    return errors


def remove_lvm_structures(  # pylint: disable=too-many-branches
    volume_groups, logical_volumes, logger=None
):
    """Remove LVM volume groups and logical volumes.

    Deactivates VGs, removes LVs, removes VGs, cleans PVs.
    Works with all LVM-capable device types.

    :param volume_groups: list of VG names
    :type volume_groups: list[str]
    :param logical_volumes: list of ``(vg_name, lv_name)`` tuples
    :type logical_volumes: list[tuple[str, str]]
    :param logger: logger instance
    :type logger: logging.Logger or None

    :return: list of error messages
    :rtype: list[str]
    """
    log = logger or LOGGER
    errors = []

    # LVM volume-group names may contain only letters, digits, underscores,
    # dots, and hyphens (lvm2 spec), and must not start with a hyphen.
    # Reject anything else to prevent shell-metacharacter injection.
    _vg_name_re = re.compile(r"^[A-Za-z0-9_.][A-Za-z0-9_.\-]*$")

    for vg in volume_groups:
        if not _vg_name_re.match(vg):
            errors.append(f"Skipping invalid VG name: {vg!r}")
            log.warning("  Rejected VG name that fails validation: %r", vg)
            continue
        result = process.run(
            f"vgs {vg} 2>/dev/null", ignore_status=True, shell=True, sudo=True
        )
        if result.exit_status:
            log.info("  VG %s not found (may have been cleaned)", vg)
            continue

        # Query PVs first, before VG removal destroys the metadata
        pvs = []
        cmd = f"pvs --noheadings -o pv_name {vg} 2>/dev/null"
        result = process.run(cmd, ignore_status=True, shell=True, sudo=True)
        if not result.exit_status and result.stdout_text.strip():
            pvs = [
                p.strip() for p in result.stdout_text.strip().split("\n") if p.strip()
            ]

        # Deactivate the VG
        result = process.run(
            f"vgchange -an {vg}", ignore_status=True, shell=True, sudo=True
        )
        if result.exit_status and "not found" not in result.stderr_text.lower():
            errors.append(f"vgchange -an {vg} failed: {result.stderr_text}")

        # Remove LVs while the VG still exists
        for vg_name, lv_name in logical_volumes:
            if vg_name == vg:
                result = process.run(
                    f"lvremove -f {vg}/{lv_name}",
                    ignore_status=True,
                    shell=True,
                    sudo=True,
                )
                if result.exit_status and "not found" not in result.stderr_text.lower():
                    errors.append(f"lvremove {vg}/{lv_name}: {result.stderr_text}")

        # Now remove the VG
        result = process.run(
            f"vgremove -f {vg}", ignore_status=True, shell=True, sudo=True
        )
        if result.exit_status and "not found" not in result.stderr_text.lower():
            errors.append(f"vgremove -f {vg} failed: {result.stderr_text}")

        # Finally clean up the PVs using the list captured before VG removal
        for pv in pvs:
            if os.path.exists(pv):
                process.run(
                    f"pvremove -ff -y {pv}", ignore_status=True, shell=True, sudo=True
                )
                result = process.run(
                    f"blkid -o value -s TYPE {pv}",
                    ignore_status=True,
                    shell=True,
                    sudo=True,
                )
                if (
                    not result.exit_status
                    and result.stdout_text.strip() == "LVM2_member"
                ):
                    process.run(
                        f"wipefs -af {pv}", ignore_status=True, shell=True, sudo=True
                    )

    for cmd in [
        "pvscan --cache",
        "udevadm trigger",
        "udevadm settle --timeout=5",
    ]:
        process.run(cmd, ignore_status=True, shell=True, sudo=True)

    return errors


def _stop_raid_array(mpath, logger):
    """Stop a single RAID array.

    Releases the device (fuser, umount), cleans metadata, then calls
    ``mdadm --stop``.

    :param mpath: absolute path to the MD device (e.g. ``/dev/md0``)
    :type mpath: str
    :param logger: logger instance
    :type logger: logging.Logger or None

    :return: tuple of ``(success, error_message)``; error_message is None on
             success
    :rtype: tuple[bool, str or None]
    """
    log = logger or LOGGER

    # Kill processes and unmount before any wipe operations so that the
    # device is not busy when wipefs/dd run.
    log.info("  Releasing %s before metadata wipe", mpath)
    process.run(
        f"fuser -9km {mpath} 2>/dev/null || true",
        ignore_status=True,
        shell=True,
        sudo=True,
    )
    time.sleep(1)

    for _ in range(UMOUNT_FORCE_RETRY_COUNT):
        process.run(
            f"umount -f -l {mpath} 2>/dev/null || true",
            ignore_status=True,
            shell=True,
            sudo=True,
        )
        time.sleep(UMOUNT_FORCE_RETRY_DELAY)

    # Device is now idle — safe to clean metadata
    log.info("  Cleaning metadata from %s", mpath)
    process.run(
        f"pvremove -ff -y {mpath} 2>/dev/null || true",
        ignore_status=True,
        shell=True,
        sudo=True,
    )
    process.run(
        f"dd if=/dev/zero of={mpath} bs=1M count=10 oflag=direct 2>/dev/null || true",
        ignore_status=True,
        shell=True,
        sudo=True,
    )
    process.run(
        f"wipefs -af {mpath} 2>/dev/null || true",
        ignore_status=True,
        shell=True,
        sudo=True,
    )
    time.sleep(1)

    result = process.run(
        f"mdadm --manage {mpath} --stop", ignore_status=True, shell=True, sudo=True
    )
    if result.exit_status:
        log.warning("  Normal stop failed, trying force stop")
        result = process.run(
            f"mdadm --stop {mpath}", ignore_status=True, shell=True, sudo=True
        )
        if result.exit_status:
            return False, result.stderr_text

    time.sleep(RAID_STOP_DELAY_SECONDS)
    try:
        gone = wait_for(
            lambda p=mpath: not os.path.exists(p),
            timeout=RAID_STOP_TIMEOUT_SECONDS,
            step=0.5,
        )
        if gone:
            log.info("  ✓ Stopped %s", mpath)
        else:
            log.warning("  %s still exists after stop timeout", mpath)
    except (OSError, TimeoutError) as e:
        log.warning("  %s may still exist after stop: %s", mpath, e)

    return True, None


def _get_raid_members(mpath):
    """Get member block devices of a RAID array.

    :param mpath: absolute path to the MD device (e.g. ``/dev/md0``)
    :type mpath: str

    :return: list of member device paths
    :rtype: list[str]
    """
    members = []
    result = process.run(
        f"mdadm --detail {mpath} 2>/dev/null", ignore_status=True, shell=True, sudo=True
    )
    if not result.exit_status:
        for line in result.stdout_text.split("\n"):
            states = ["active sync", "spare", "faulty"]
            if "/dev/" in line and any(s in line for s in states):
                for p in line.split():
                    if p.startswith("/dev/"):
                        members.append(p)
                        break
    return members


def _clean_raid_members(members, logger):
    """Clear RAID superblocks and filesystem signatures from member devices.

    :param members: list of member device paths
    :type members: list[str]
    :param logger: logger instance
    :type logger: logging.Logger or None
    """
    log = logger or LOGGER
    for m in members:
        if os.path.exists(m):
            process.run(
                f"mdadm --zero-superblock {m}",
                ignore_status=True,
                shell=True,
                sudo=True,
            )
            process.run(f"wipefs -af {m}", ignore_status=True, shell=True, sudo=True)
            result = process.run(
                f"blkid -o value -s TYPE {m}",
                ignore_status=True,
                shell=True,
                sudo=True,
            )
            if not result.exit_status and result.stdout_text.strip() == "LVM2_member":
                log.info("  Cleaning LVM metadata from %s", m)
                process.run(
                    f"wipefs -af {m}", ignore_status=True, shell=True, sudo=True
                )


def cleanup_raid_arrays(raid_devices, logger=None):
    """Stop RAID arrays and clean member devices.

    Works with software RAID (md) regardless of underlying device type.

    :param raid_devices: list of RAID device paths
    :type raid_devices: list[str]
    :param logger: logger instance
    :type logger: logging.Logger or None

    :return: list of error messages
    :rtype: list[str]
    """
    log = logger or LOGGER
    errors = []

    for md in raid_devices:
        mpath = get_absolute_disk_path(md)
        cmd = f"mdadm --detail --test {mpath} 2>/dev/null"
        result = process.run(cmd, ignore_status=True, shell=True, sudo=True)
        if result.exit_status or not os.path.exists(mpath):
            continue

        log.info("  Stopping %s", mpath)

        umount_errs = unmount_devices([mpath], log, is_swap=False)
        if umount_errs:
            for err in umount_errs:
                log.warning("  %s", err)

        members = _get_raid_members(mpath)
        success, error = _stop_raid_array(mpath, log)

        if not success:
            errors.append(f"Failed to stop {mpath}: {error}")
            log.error("  ✗ Failed to stop %s", mpath)
            continue

        result = process.run(
            f"blkid -o value -s TYPE {mpath}",
            ignore_status=True,
            shell=True,
            sudo=True,
        )
        if not result.exit_status and result.stdout_text.strip() == "LVM2_member":
            log.info("  Cleaning LVM metadata from %s", mpath)
            process.run(
                f"wipefs -af {mpath}", ignore_status=True, shell=True, sudo=True
            )

        _clean_raid_members(members, log)

    return errors


def wipe_disk_metadata(devices, logger=None):
    """Wipe filesystem and partition metadata from devices.

    Works with all device types (NVMe, SCSI, IDE, virtio, multipath).

    :param devices: list of device names to wipe
    :type devices: list[str]
    :param logger: logger instance
    :type logger: logging.Logger or None

    :return: list of error messages (empty if successful)
    :rtype: list[str]
    """
    log = logger or LOGGER
    errors = []

    for dev in devices:
        dpath = get_absolute_disk_path(dev)
        if not os.path.exists(dpath):
            continue

        # Kill processes using the device
        process.run(
            f"fuser -9km {dpath} 2>/dev/null || true",
            ignore_status=True,
            shell=True,
            sudo=True,
        )
        process.run(
            f"blockdev --flushbufs {dpath} 2>/dev/null || true",
            ignore_status=True,
            shell=True,
            sudo=True,
        )

        # Handle device-mapper devices
        is_dm = "mapper" in dpath or dpath.startswith("/dev/dm-")
        if is_dm:
            dm_name = os.path.basename(dpath)
            if not dm_name.startswith("dm-"):
                dm_name = dm_name.replace("mapper/", "")
            process.run(
                f"dmsetup suspend {dm_name} 2>/dev/null || true",
                ignore_status=True,
                shell=True,
                sudo=True,
            )
            time.sleep(DM_SUSPEND_DELAY_SECONDS)
            process.run(
                f"dmsetup resume {dm_name} 2>/dev/null || true",
                ignore_status=True,
                shell=True,
                sudo=True,
            )
            time.sleep(DM_SUSPEND_DELAY_SECONDS)

        # Wipe with retry — stderr must not be redirected so process.run
        # can capture it for the "Device or resource busy" retry check.
        for attempt in range(WIPE_RETRY_ATTEMPTS):
            result1 = process.run(
                f"wipefs -af {dpath}",
                ignore_status=True,
                shell=True,
                sudo=True,
            )

            process.run(
                f"dd if=/dev/zero of={dpath} bs=512 count=2048 "
                f"oflag=direct 2>/dev/null || true",
                ignore_status=True,
                shell=True,
                sudo=True,
            )

            result2 = process.run(
                f"wipefs -a {dpath}",
                ignore_status=True,
                shell=True,
                sudo=True,
            )

            if (
                not result1.exit_status
                or not result2.exit_status
                or "Device or resource busy" not in result2.stderr_text
            ):
                # Exit the retry loop; report failure when neither wipe
                # succeeded.
                if result1.exit_status and result2.exit_status:
                    errors.append(
                        f"wipefs failed on {dpath}: {result2.stderr_text.strip()}"
                    )
                break

            if attempt < WIPE_RETRY_ATTEMPTS - 1:
                log.debug(
                    "  Wipe retry %s/%s for %s",
                    attempt + 1,
                    WIPE_RETRY_ATTEMPTS,
                    dpath,
                )
                time.sleep(WIPE_RETRY_DELAY_SECONDS)
            else:
                errors.append(
                    f"{dpath} still busy after {WIPE_RETRY_ATTEMPTS} wipe attempts"
                )

    return errors


def _remove_partition_tables(disks, logger=None):
    """Remove partition tables from disks.

    :param disks: list of disk device names
    :type disks: list[str]
    :param logger: logger instance (unused, kept for API consistency)
    :type logger: logging.Logger or None

    :return: list of error messages
    :rtype: list[str]
    """
    _ = logger  # Unused but kept for API consistency
    errors = []
    for dev in disks:
        dpath = get_absolute_disk_path(dev)
        if os.path.exists(dpath):
            result = process.run(
                f"sgdisk --zap-all {dpath} 2>/dev/null",
                ignore_status=True,
                shell=True,
                sudo=True,
            )
            if result.exit_status:
                result = process.run(
                    f"dd if=/dev/zero of={dpath} bs=512 count=1 2>/dev/null",
                    ignore_status=True,
                    shell=True,
                    sudo=True,
                )
                if result.exit_status:
                    errors.append(f"Failed partition removal on {dev}")
    return errors


def _zero_disks(disks, logger, wipe_size_mb=DEFAULT_WIPE_SIZE_MB):
    """Zero the first N MB of each disk.

    :param disks: list of disk device names
    :type disks: list[str]
    :param logger: logger instance
    :type logger: logging.Logger
    :param wipe_size_mb: number of megabytes to zero from the start of each
                         disk
    :type wipe_size_mb: int
    """
    for dev in disks:
        dpath = get_absolute_disk_path(dev)
        if not os.path.exists(dpath):
            continue
        logger.debug("  Zeroing first %sMB of %s", wipe_size_mb, dev)
        process.run(
            f"dd if=/dev/zero of={dpath} bs=1M count={wipe_size_mb} oflag=direct",
            ignore_status=True,
            shell=True,
            sudo=True,
        )


def _settle_system(logger):
    """Settle the system after disk operations.

    Runs ``sync``, ``udevadm settle``, and ``partprobe``, then waits for
    device stabilization.

    :param logger: logger instance
    :type logger: logging.Logger
    """
    logger.info("\n[FINAL] Settling system")
    process.run("sync", ignore_status=True, shell=True, sudo=True)
    process.run(
        f"udevadm settle --timeout={UDEV_SETTLE_TIMEOUT_SECONDS}",
        ignore_status=True,
        shell=True,
        sudo=True,
    )
    process.run(
        "partprobe 2>/dev/null || true", ignore_status=True, shell=True, sudo=True
    )
    time.sleep(DEVICE_STABILIZATION_DELAY_SECONDS)
    logger.info(
        "  Waited %ss for device stabilization", DEVICE_STABILIZATION_DELAY_SECONDS
    )


def cleanup_disks(  # pylint: disable=too-many-branches,too-many-statements
    disk_list, logger=None, mode="light"
):
    """Comprehensive disk cleanup with automatic dependency detection.

    Main entry point for disk cleanup. Performs intelligent cleanup including
    multipath normalization, dependency detection, unmounting, LVM/RAID
    removal, and optional partition/metadata wiping.

    :param disk_list: list of disk identifiers (bare names or ``/dev/`` paths)
    :type disk_list: list[str]
    :param logger: logger instance (uses module logger if None)
    :type logger: logging.Logger or None
    :param mode: cleanup depth — ``"light"`` preserves partitions,
        ``"full"`` performs complete wipe, ``"auto"`` detects from device
        state. Raises :exc:`ValueError` for any other value.
    :type mode: str

    :return: tuple of ``(success, errors)``
    :rtype: tuple[bool, list[str]]
    :raises ValueError: if ``mode`` is not one of the valid modes or if
                        ``disk_list`` contains unresolvable device entries
    """
    log = logger or LOGGER
    errors = []

    if not disk_list:
        return False, ["No disks provided"]

    if mode not in CLEANUP_DISK_VALID_MODES:
        raise ValueError(
            f"Invalid mode {mode!r}. Must be one of: "
            f"{sorted(CLEANUP_DISK_VALID_MODES)}"
        )

    invalid = [d for d in disk_list if not os.path.exists(get_absolute_disk_path(d))]
    if invalid:
        raise ValueError(
            f"disk_list contains unresolvable entries: {invalid}. "
            f"Each entry must be a bare device name (e.g. 'sda', 'nvme0n1') "
            f"or an absolute path under /dev/ that exists on this system "
            f"(e.g. '/dev/sda', '/dev/mapper/mpatha', "
            f"'/dev/disk/by-id/<id>')."
        )

    log.info("%s\nDISK CLEANUP - Starting\n%s", "=" * 70, "=" * 70)
    log.info("Input: %s, Mode: %s", disk_list, mode)

    # Step 1: Normalize multipath devices
    log.info("\n[1/9] Normalizing multipath")
    disks = normalize_multipath_devices(disk_list, log)
    log.info("Target: %s", disks)

    # Step 2: Build dependency graph
    log.info("\n[2/9] Building dependency graph")
    deps = build_device_dependencies(disks, log)
    log.info(
        "Found: %d parts, %d VGs, %d LVs, %d PVs, %d RAIDs",
        len(deps["parts"]),
        len(deps["vgs"]),
        len(deps["lvs"]),
        len(deps["pvs"]),
        len(deps["raids"]),
    )

    # Auto-detect mode if requested
    if mode == "auto":
        has_structures = bool(deps["parts"] or deps["vgs"] or deps["raids"])
        # Use "full" when structures exist so they are completely removed;
        # use "light" only when the disk is already clean.
        mode = "full" if has_structures else "light"
        status = "structures found — full wipe" if has_structures else "disk clean"
        log.info("\n[AUTO-DETECT] Mode: %s (%s)", mode.upper(), status)

    # Step 3: Unmount filesystems
    log.info("\n[3/9] Unmounting filesystems")
    errors.extend(unmount_devices(deps["mounts"], log, is_swap=False))

    # Step 4: Disable swap
    log.info("\n[4/9] Disabling swap")
    errors.extend(unmount_devices(deps["devs"], log, is_swap=True))

    # Step 5: Remove LVM
    if deps["vgs"]:
        log.info("\n[5/9] Removing LVM: %s", deps["vgs"])
        errors.extend(remove_lvm_structures(deps["vgs"], deps["lvs"], log))
    else:
        log.info("\n[5/9] No LVM - skipping")

    # Step 6: Cleanup RAID
    if deps["raids"]:
        log.info("\n[6/9] Cleaning RAID: %s", deps["raids"])
        errors.extend(cleanup_raid_arrays(deps["raids"], log))
    else:
        log.info("\n[6/9] No RAID - skipping")

    # Steps 7-9: Mode-dependent operations
    if mode == "full":
        log.info("\n[7/9] Removing partition tables")
        errors.extend(_remove_partition_tables(disks, log))

        log.info("\n[8/9] Wiping metadata")
        errors.extend(wipe_disk_metadata(disks, log))

        log.info("\n[9/9] Zeroing disks")
        _zero_disks(disks, log, wipe_size_mb=100)

        _settle_system(log)
    else:
        log.info("\n[7/9] Skipping partition removal (light mode)")
        log.info("\n[8/9] Wiping metadata (partition/LV/RAID members only)")
        # Light mode preserves the whole-disk partition table.  Wipe only the
        # discovered sub-devices (partitions, LV block devices, RAID members)
        # so that partition-table signatures on the parent disk are untouched.
        lv_devs = [f"{vg}-{lv}" for vg, lv in deps["lvs"]]
        sub_devices = list(set(deps["parts"] + lv_devs + list(deps["raids"])))
        if sub_devices:
            errors.extend(wipe_disk_metadata(sub_devices, log))
        else:
            log.info("  No sub-devices found; skipping metadata wipe")
        log.info("\n[9/9] Skipping disk zeroing (light mode)")
        log.info("  → Disk ready for RAID/LVM creation")

    success = not errors
    log.info("\n%s", "=" * 70)
    if success:
        log.info("✓ SUCCESS")
    else:
        log.info("✗ %s ERRORS", len(errors))
    if errors:
        for i, e in enumerate(errors, 1):
            log.warning("  %s. %s", i, e)
    log.info("%s", "=" * 70)

    return success, errors
