#!/usr/bin/env python3
"""Assert the image/runtime conditions behind the scoped scanner dispositions."""
import os
from pathlib import Path
import shutil
import stat


def unreadable(error):
    raise error


def main():
    assert os.getuid() != 0 and os.geteuid() != 0, 'Root runtime is unsupported'
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    assert int(status['NoNewPrivs'].strip()) == 1, 'Privilege escalation must be disabled'
    assert int(status['CapEff'].strip(), 16) == 0, 'Runtime must have no effective capabilities'
    for command in ('mount', 'umount', 'nsenter', 'infocmp', 'systemd-homed', 'npm', 'npx'):
        assert shutil.which(command) is None, f'Unexpected runtime tool: {command}'
    for directory in ('/usr', '/bin', '/sbin', '/lib', '/lib64'):
        for parent, dirs, files in os.walk(directory, followlinks=False, onerror=unreadable):
            for name in files:
                path = Path(parent, name)
                assert name != 'systemd-homed', 'systemd-homed is outside the supported image'
                assert not (name == 'Tar.pm' and path.parent.name == 'Archive'), 'Unexpected Archive::Tar'
                if path.is_symlink():
                    continue
                info = path.stat()
                assert not info.st_mode & (stat.S_ISUID | stat.S_ISGID), f'Privileged file: {path}'
                assert 'security.capability' not in os.listxattr(path), f'File capability: {path}'
    print('Verified absent tools/modules and unprivileged runtime conditions')


if __name__ == '__main__':
    main()
