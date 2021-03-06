#!/usr/bin/python3
# coding: utf-8

"""
---
module: tree_permission
short_description: set recursively fs permissions
author: Johannes Drummer <johannes.drummer@it-novum.com>
description:
  - Uses regular expressions to check the complete file system path
  - The full regex for path matching is: ^{root_path}{path}$
  - All directories end with a slash /, files not
  - To match the root_path, just use a "/" as regex
  - All files and directories inside the root_path will be checked one by one, so you shouldn't set / as the root_path!
  - For a natural behaviour the regexes will be checked in reversed order (from bottom to top)
  - The first regex that matches counts
  - Only the attributes that are specified will be changed
  - You may exclude files/folders by specifying them at the end of the list without any attributes
options:
  root_path:
    description:
      - main level
      - Path to a directory (not a regex)
      - This path is prepended to the path in the regex definition
    required: True
  regexp:
    description:
      - main level
      - list of regex definitions
      - the list will be checked in reversed order
      - the first match will be used
    required: True
  regex_definition:
    description:
      - inside regexp list
      - contains regexes and attribute information
    required: True
  paths:
    description:
      - inside a regex_definition
      - a list of regex strings that will be appended to root_path
      - a $ will be automatically appended to the path!
      - see python documentation for more information about the re module
    required: True
  file_mode:
    description:
      - inside regex_definition
      - ensures the mode of all matching files
      - must an octal number (will be passed to chmod 1:1)
    required: False
  dir_mode:
    description:
      - inside regex_definition
      - ensures the mode of all matching directories
      - must an octal number (will be passed to chmod 1:1)
    required: False
  file_owner:
    description:
      - inside regex_definition
      - ensures the owner of all matching files
      - must be an existing user name (not uid)
    required: False
  file_group:
    description:
      - inside regex_definition
      - ensures the owning group of all matching files
      - must be an existing group name (not gid)
    required: False
  dir_owner:
    description:
      - inside regex_definition
      - ensures the owner of all matching directories
      - must be an existing user name (not uid)
    required: False
  dir_group:
    description:
      - inside regex_definition
      - ensures the owning group of all matching directories
      - must be an existing group name (not gid)
    required: False
  do_files:
    description:
      - inside regex_definition
      - if false don't check files
    required: False
    default: True
  do_dirs:
    description:
      - inside regex_definition
      - if false don't check dirs
    required: False
    default: True
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type


EXAMPLES = """
- tree_permission:
    root_path: /path/to/htdocs
    regexp:
      - paths:
          - ".*"
        file_mode: 0644
        dir_mode: 0755
        file_owner: idoit
        file_group: idoit
        dir_owner: idoit
        dir_group: idoit
      - paths:
          - "/"
        dir_mode: 0750
        dir_owner: idoit
        dir_group: idoit
      - paths:
          - "/checkmk_transfer.sh"
          - "/controller"
          - "/idoit-rights.sh"
          - "/import"
          - "/tenants"
          - "/updatecheck"
        file_mode: 0755
        file_owner: idoit
        file_group: idoit

- tree_permission:
    root_path: /path/to/otrs
    regexp:
      - paths:
          - ".*"
        file_mode: 0644
        dir_mode: 0755
        file_owner: otrs
        file_group: otrs
        dir_owner: otrs
        dir_group: otrs
      - paths:
          - "/bin/.*"
        file_mode: 0755
        file_owner: otrs
        file_group: otrs
        do_dirs: false
"""


# WANT_JSON

import json
import sys
import os
from collections import OrderedDict
import re
import pwd
import grp

debug = False
changed_list = []

# Py2/3
try:
  basestring
except NameError:
  basestring = str


def add_to_changed_list(path):
    if debug:
        changed_list.append(path)

def fail_json(msg, **args):
    json.dump(dict(failed=True, msg=msg, **args), sys.stdout)
    sys.exit(0)


def normpath(path):
    abspath = os.path.abspath(path)
    if os.path.isdir(abspath):
        return abspath + '/'
    else:
        return abspath


def collect_path_data(path):
    path_stat = os.lstat(path)
    is_dir = os.path.isdir(path)
    return {
        "path": path,
        "mode": path_stat.st_mode & 4095, # octal 0777
        "uid": path_stat.st_uid,
        "gid": path_stat.st_gid,
        "isdir": is_dir,
        "isfile": not is_dir,
    }


def iterate_fstree(path):
    rootpath = normpath(path)
    if os.path.exists(rootpath):
        yield collect_path_data(rootpath)
        for root, dirs, files in os.walk(rootpath):
            allpaths = dirs + files
            for current_path in allpaths:
                yield collect_path_data(normpath(os.path.join(root, current_path)))


def try_kwarg(kwargs, key, default_value=None, required=False):
    try:
        return kwargs[key]
    except KeyError as e:
        if required:
            raise e
        else:
            return default_value


def to_bool(arg):
    ''' return a bool for the arg '''
    if arg is None or isinstance(arg, bool):
        return arg
    if isinstance(arg, basestring):
        arg = arg.lower()
    if arg in ['y', 'yes', 'on', '1', 'true', 1, True]:
        return True
    elif arg in ['n', 'no', 'off', '0', 'false', 0, False]:
        return False
    else:
        fail_json(msg='{} is not a valid boolean!'.format(str(arg)))


def to_mode(value):
    if value is None:
        return value
    if not isinstance(value, int):
        try:
            value = int(value, 8)
        except (ValueError, TypeError):
            fail_json('Not an integer: {}'.format(str(value)))
    return value


class PermissionRegex:

    def __init__(self, root_path, kwargs):
        self.paths = try_kwarg(kwargs, 'paths', required=True)
        self.file_mode = to_mode(try_kwarg(kwargs, 'file_mode'))
        self.dir_mode = to_mode(try_kwarg(kwargs, 'dir_mode'))
        self.file_owner = try_kwarg(kwargs, 'file_owner')
        self.file_group = try_kwarg(kwargs, 'file_group')
        self.dir_owner = try_kwarg(kwargs, 'dir_owner')
        self.dir_group = try_kwarg(kwargs, 'dir_group')

        self.do_files = to_bool(try_kwarg(kwargs, 'do_files', True))
        self.do_dirs = to_bool(try_kwarg(kwargs, 'do_dirs', True))

        try:
            if self.file_owner is not None:
                self.file_uid = pwd.getpwnam(self.file_owner).pw_uid
            else:
                self.file_uid = None
            if self.file_group is not None:
                self.file_gid = grp.getgrnam(self.file_group).gr_gid
            else:
                self.file_gid = None
            if self.dir_owner is not None:
                self.dir_uid = pwd.getpwnam(self.dir_owner).pw_uid
            else:
                self.dir_uid = None
            if self.dir_group is not None:
                self.dir_gid = grp.getgrnam(self.dir_group).gr_gid
            else:
                self.dir_gid = None
        except KeyError as e:
            fail_json('Could not find user or group: {}'.format(e.message))

        self.regex_paths = []

        for path in self.paths:
            self.regex_paths.append(re.compile('^{}{}$'.format(root_path, path)))

    def check_path(self, fspath):
        if fspath['isfile'] and not self.do_files:
            return False
        if fspath['isdir'] and not self.do_dirs:
            return False
        for regex in self.regex_paths:
            match = regex.match(fspath['path'])
            if match is not None:
                return True
        return False

    def apply(self, path_data, change_mode, changed):
        if path_data['isfile']:
            if self.file_mode is not None and path_data['mode'] != self.file_mode:
                changed = True
                add_to_changed_list(path_data['path'])
                if change_mode:
                    os.chmod(path_data['path'], self.file_mode)
                    path_data['mode'] = self.file_mode
            if self.file_uid is not None and path_data['uid'] != self.file_uid:
                changed = True
                add_to_changed_list(path_data['path'])
                if change_mode:
                    os.chown(path_data['path'], self.file_uid, path_data['gid'])
                    path_data['uid'] = self.file_uid
            if self.file_gid is not None and path_data['gid'] != self.file_gid:
                changed = True
                add_to_changed_list(path_data['path'])
                if change_mode:
                    os.chown(path_data['path'], path_data['uid'], self.file_gid)
                    path_data['gid'] = self.file_gid
        elif path_data['isdir']:
            if self.dir_mode is not None and path_data['mode'] != self.dir_mode:
                changed = True
                add_to_changed_list(path_data['path'])
                if change_mode:
                    os.chmod(path_data['path'], self.dir_mode)
                    path_data['mode'] = self.dir_mode
            if self.dir_uid is not None and path_data['uid'] != self.dir_uid:
                changed = True
                add_to_changed_list(path_data['path'])
                if change_mode:
                    os.chown(path_data['path'], self.dir_uid, path_data['gid'])
                    path_data['uid'] = self.dir_uid
            if self.dir_gid is not None and path_data['gid'] != self.dir_gid:
                changed = True
                add_to_changed_list(path_data['path'])
                if change_mode:
                    os.chown(path_data['path'], path_data['uid'], self.dir_gid)
                    path_data['gid'] = self.dir_gid
        return changed


def main():
    global debug

    args = None
    with open(sys.argv[1]) as f:
        args = json.load(f, encoding='UTF-8', object_pairs_hook=OrderedDict)
    change_mode = True
    changed = False

    try:
        debug = to_bool(args['debug'])
    except KeyError:
        debug = False

    try:
        root_path = args['root_path']
        if not os.path.isdir(root_path):
            fail_json('{} must be a directory'.format(root_path))
    except KeyError:
        fail_json('Missing root_path argument!')

    try:
        if not isinstance(args['regexp'], list):
            fail_json('regexp must be a list of path regex definitions!')
    except KeyError:
        fail_json('Missing regexp argument!')

    regexp = []

    for data in args['regexp']:
        if not isinstance(data, dict):
            fail_json('regexp must be a list of path regex definitions!')
        regexp.insert(0, PermissionRegex(args['root_path'], data))

    for fspath in iterate_fstree(args['root_path']):
        for pr in regexp:
            if pr.check_path(fspath):
                changed = pr.apply(fspath, change_mode, changed)
                break

    result = {'changed': changed}
    if debug:
        result['changed_list'] = changed_list
    json.dump(result, sys.stdout)


if __name__ == '__main__':
    main()