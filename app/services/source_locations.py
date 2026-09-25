"""Observed Linux filesystem identity for bounded curation sources.

No mount, probe, enumeration, privilege escalation, or caller UUID fallback.
The local operator and the OS mount namespace remain trusted boundaries.
"""
import json
import os
from pathlib import Path
import re
import subprocess

from app.services.curation_policy import fail

PRODUCTION_POLICY_VERSION = 'production-stills-v1'
FIXTURE_POLICY_VERSION = 'fixture-stills-v1'
_COLUMNS = 'UUID,TARGET,SOURCE,FSTYPE,MAJ:MIN,FSROOT,ID'


def _unescape_mount(value: str) -> str:
    return re.sub(r'\\(040|011|012|134)', lambda m: chr(int(m[1], 8)), value)


def _mount_table() -> dict[int, dict]:
    """Read kernel mount metadata only; octal escapes must stay structural."""
    try:
        rows = {}
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            left, right = line.split(' - ', 1)
            fields, fs = left.split(), right.split()
            mount_id = int(fields[0])
            rows[mount_id] = {'mount_id': mount_id, 'device': fields[2],
                'filesystem_root': _unescape_mount(fields[3]),
                'mountpoint': _unescape_mount(fields[4]),
                'filesystem_type': fs[0], 'source': _unescape_mount(fs[1])}
        return rows
    except (OSError, ValueError, IndexError):
        fail('source_volume_identity_unavailable')


def _namespace_identity() -> tuple[int, int]:
    try:
        info = os.stat('/proc/self/ns/mnt')
        return info.st_dev, info.st_ino
    except OSError:
        fail('source_volume_identity_unavailable')


def descriptor_mount_id(fd: int) -> int:
    try:
        lines = Path(f'/proc/self/fdinfo/{fd}').read_text().splitlines()
        values = [line.split(':', 1)[1].strip() for line in lines if line.startswith('mnt_id:')]
        if len(values) != 1:
            fail('source_volume_identity_unavailable')
        return int(values[0])
    except (OSError, ValueError):
        fail('source_volume_identity_unavailable')


def ensure_disjoint_roots(source_path: str, source_fd: int,
                          store_path: str, store_fd: int) -> None:
    """Reject lexical and bind-mount aliases of ancestor/descendant roots."""
    paths = [Path(source_path), Path(store_path)]
    if paths[0] == paths[1] or paths[0] in paths[1].parents or paths[1] in paths[0].parents:
        fail('overlapping_roots')
    mounts = _mount_table()
    locations = []
    for path, fd in zip(paths, (source_fd, store_fd)):
        row = mounts.get(descriptor_mount_id(fd))
        if row is None or not path.is_relative_to(row['mountpoint']):
            fail('source_volume_changed')
        relative = path.relative_to(row['mountpoint'])
        locations.append((row['device'], Path(row['filesystem_root']) / relative))
    if locations[0][0] == locations[1][0]:
        a, b = locations[0][1], locations[1][1]
        if a == b or a in b.parents or b in a.parents:
            fail('overlapping_roots')


def observe_source_volume(path: str, *, opened_fd: int | None = None) -> dict:
    """Observe UUID with findmnt, cross-check kernel mount identity and descriptor.

    Missing UUID (common inside containers), missing tools, inconsistent metadata,
    detached mounts and mount namespace changes are hard failures. findmnt is
    invoked without a shell and with a bounded timeout; its output is never logged.
    """
    if not Path(path).is_absolute():
        fail('unsafe_root')
    namespace_before = _namespace_identity()
    mounts_before = _mount_table()
    try:
        result = subprocess.run(['findmnt', '--json', '--first-only', '--target', path,
            '--output', _COLUMNS], capture_output=True, text=True, timeout=5, check=False)
        if result.returncode != 0:
            fail('source_volume_identity_unavailable')
        rows = json.loads(result.stdout)['filesystems']
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            fail('source_volume_identity_unavailable')
        row = rows[0]
        uuid = row.get('uuid')
        if not isinstance(uuid, str) or not re.fullmatch(r'[A-Za-z0-9.-]{1,128}', uuid):
            fail('source_volume_uuid_unavailable')
        observed = {'mount_id': int(row['id']), 'device': row['maj:min'],
            'filesystem_root': row['fsroot'], 'mountpoint': row['target'],
            'filesystem_type': row['fstype'], 'source': row['source']}
        # SOURCE's optional [/subvolume] suffix is presentation, not the device.
        kernel = mounts_before.get(observed['mount_id'])
        if kernel is None:
            fail('source_volume_changed')
        if observed['source'] == kernel['source'] + '[' + kernel['filesystem_root'] + ']':
            observed['source'] = kernel['source']
        if observed != kernel or _mount_table().get(observed['mount_id']) != kernel:
            fail('source_volume_changed')
        if _namespace_identity() != namespace_before:
            fail('source_volume_changed')
        if not Path(path).is_relative_to(observed['mountpoint']):
            fail('source_volume_changed')
        if opened_fd is not None:
            # Btrfs subvolume st_dev is allowed to differ from mountinfo's
            # synthetic filesystem device; the kernel mount id is authoritative.
            if descriptor_mount_id(opened_fd) != observed['mount_id']:
                fail('source_volume_changed')
        return {**observed, 'uuid': uuid.casefold(),
                'namespace_device': namespace_before[0], 'namespace_inode': namespace_before[1],
                'observer': 'findmnt+proc-mountinfo-v1'}
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        fail('source_volume_identity_unavailable')


def _volume_policy(dataset) -> dict | None:
    policy = dataset.policy
    if not isinstance(policy, dict):
        fail('source_volume_policy_required')
    if (dataset.policy_version == FIXTURE_POLICY_VERSION
            and policy.get('version') == FIXTURE_POLICY_VERSION
            and policy.get('fixture_only') is True
            and policy.get('generative_enabled') is False):
        return None
    if (dataset.policy_version != PRODUCTION_POLICY_VERSION
            or policy.get('version') != PRODUCTION_POLICY_VERSION
            or policy.get('fixture_only') is not False
            or policy.get('generative_enabled') is not False):
        fail('source_volume_policy_required')
    volume = policy.get('source_volume')
    required = {'mount_id', 'device', 'filesystem_root', 'mountpoint', 'filesystem_type',
                'source', 'uuid', 'namespace_device', 'namespace_inode', 'observer'}
    if not isinstance(volume, dict) or set(volume) != required:
        fail('source_volume_policy_required')
    return volume


# Fields that identify the filesystem itself. A different value here is a
# different volume, and no operator action may relocate a dataset onto it.
PERSISTENT_VOLUME_FIELDS = ('uuid', 'filesystem_type', 'filesystem_root', 'mountpoint', 'observer')
# Fields that legitimately change when the runtime restarts or a device is
# re-plugged (new mount id, mount namespace, device node). They still fence
# every read; they may only be re-pinned by an explicit, audited operator
# re-attestation that re-verifies every registered file.
RUNTIME_VOLUME_FIELDS = ('mount_id', 'device', 'source', 'namespace_device', 'namespace_inode')
# Filesystems whose Linux drivers assign inode numbers with iunique() when an
# inode is instantiated (fs/exfat/inode.c, fs/fat/inode.c). The number changes
# after cache eviction or a remount, so it cannot identify a directory across
# reads. Registration records this and the source-root inode fence is then
# carried by the volume UUID, filesystem root, mountpoint, the pinned relative
# paths and the per-file hashes instead; every other fence is unchanged. The
# derivative store is never on such a filesystem (the capability probe refuses
# it), so its inode fence always applies.
NON_PERSISTENT_INODE_FILESYSTEMS = frozenset({'exfat', 'vfat', 'msdos'})
_ROOT_IDENTITY_FIELDS = {'basis', 'filesystem_type', 'source_inode_persistent', 'store_inode_persistent'}


def root_identity_record(volume: dict) -> dict:
    """What registration records about whether root inode numbers can be trusted."""
    return {'basis': 'filesystem_type', 'filesystem_type': volume['filesystem_type'],
            'source_inode_persistent': volume['filesystem_type'] not in NON_PERSISTENT_INODE_FILESYSTEMS,
            'store_inode_persistent': True}


def source_root_identity(dataset) -> tuple[int, int | None]:
    """Device and, when the filesystem keeps them, inode of the pinned source root.

    Datasets registered before the record existed keep the strict inode fence.
    A record that disagrees with the pinned filesystem type is invalid: the
    fence may not be relaxed by editing policy.
    """
    expected = _volume_policy(dataset)
    record = dataset.policy.get('root_identity') if expected is not None else None
    if record is None:
        return dataset.source_device, dataset.source_inode
    if (not isinstance(record, dict) or set(record) != _ROOT_IDENTITY_FIELDS
            or record != root_identity_record(expected)):
        fail('source_volume_policy_required')
    if record['source_inode_persistent']:
        return dataset.source_device, dataset.source_inode
    return dataset.source_device, None


def registered_source_volume(dataset) -> dict:
    """The pinned production volume identity; fixture datasets have none."""
    expected = _volume_policy(dataset)
    if expected is None:
        fail('production_authority_required', 403)
    return expected


def compare_source_volume(expected: dict, observed: dict) -> dict:
    """Split a fresh observation into persistent mismatches and runtime drift."""
    return {'persistent_mismatch': [k for k in PERSISTENT_VOLUME_FIELDS if observed.get(k) != expected.get(k)],
            'runtime_drift': [k for k in RUNTIME_VOLUME_FIELDS if observed.get(k) != expected.get(k)]}


def verify_source_descriptor(dataset, fd: int) -> None:
    """Fence every original file descriptor before and after reading its bytes."""
    expected = _volume_policy(dataset)
    if expected is None:
        return
    info = os.fstat(fd)
    if (descriptor_mount_id(fd) != expected['mount_id']
            or info.st_dev != dataset.source_device
            or _namespace_identity() != (expected['namespace_device'], expected['namespace_inode'])):
        fail('source_volume_changed')


def verify_source_volume(dataset, relative_path: str | None = None) -> dict | None:
    """Re-observe a dataset root and optionally the exact file's mounted path.

    Call around every read, including readbacks. Pair with the descriptor guard
    to reject same-device bind mounts and substitutions during descriptor capture.
    """
    # Lazy import avoids the artifacts -> source_locations integration cycle.
    from app.services.curation_artifacts import directory, safe_parts

    expected = _volume_policy(dataset)
    if expected is None:
        return None
    with directory(dataset.source_root, source_root_identity(dataset)) as fd:
        observed = observe_source_volume(dataset.source_root, opened_fd=fd)
        if observed != expected:
            fail('source_volume_changed')
        verify_source_descriptor(dataset, fd)
    if relative_path is not None:
        safe_parts(relative_path)
        if observe_source_volume(str(Path(dataset.source_root) / relative_path)) != expected:
            fail('source_volume_changed')
    return observed
