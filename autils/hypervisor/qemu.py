#
# Library for qemu option related helper functions
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; specifically version 2 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
#
# See LICENSE for more details.
#
# Copyright: Red Hat (c) 2024 and Avocado contributors
# Author: Yongxue Hong <yhong@redhat.com>

"""QEMU hypervisor option related helper functions.

This module provides utility functions for working with QEMU/KVM hypervisor
options and capabilities, including checking for supported command-line options
and retrieving available machine types.
"""
import re

from autils.devel import process


def has_option(option, qemu_path="/usr/bin/qemu-kvm"):
    """Check if QEMU supports a specific command line option.

    This function queries the QEMU help output to determine if a specific
    command line option is supported by the given QEMU binary.

    :param option: Command line option to check (without leading dash).
    :type option: str
    :param qemu_path: Path to the QEMU binary to check.
    :type qemu_path: str
    :return: True if the option is supported, False otherwise.
    :rtype: bool
    """
    hlp = process.run(
        f"{qemu_path} -help", shell=True, ignore_status=True, verbose=False
    ).stdout_text
    return bool(re.search(rf"^-{option}(\s|$)", hlp, re.MULTILINE))


def get_support_machine_type(qemu_binary="/usr/libexec/qemu-kvm", remove_alias=False):
    """Get machine types supported by the QEMU binary.

    This function queries the QEMU binary for available machine types and
    parses the output to return organized lists of machine names, descriptions,
    and aliases.

    :param qemu_binary: Path to the QEMU binary to query.
    :type qemu_binary: str
    :param remove_alias: If True, exclude alias information from results.
    :type remove_alias: bool
    :return: A tuple containing three lists: (machine_names, machine_types, machine_aliases).
             Each list contains strings corresponding to the machine name, description,
             and alias information respectively.
    :rtype: tuple[list[str], list[str], list[str or None]]
    """
    o = process.run(f"{qemu_binary} -M ?").stdout_text.splitlines()
    machine_name = []
    machine_type = []
    machine_alias = []
    split_pattern = re.compile(
        r"^(\S+)\s+(.*?)(?: (\((?:alias|default|deprecated).*))?$"
    )
    for item in o[1:]:
        if "none" in item:
            continue
        machine_list = split_pattern.search(item).groups()
        machine_name.append(machine_list[0])
        machine_type.append(machine_list[1])
        val = None if remove_alias else machine_list[2]
        machine_alias.append(val)
    return machine_name, machine_type, machine_alias
