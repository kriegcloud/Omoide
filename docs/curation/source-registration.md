# Bounded source registration and legacy proposals

Production still registration is a trusted **local operator capability**. It is
not an HTTP endpoint and issues no grant, enrollment, credential, review or
acceptance. `register_source_manifest(session, manifest)` accepts one explicit
manifest of 1–100 files. The caller must already have authorization for exactly
those files and supply a dedicated clean SQLModel session against the intended
migrated database. Registration commits its own transaction; it never opens the
application's configured database itself. This implementation does not authorize
use of the shared library or production database.

The returned object has `dataset_id`, `source_count`, `policy_version`, and
`authorization_manifest_sha256`. It contains no secrets. Human/agent grants and
passkey enrollment are separate local authority operations. The dataset policy
is `production-stills-v1`, with exact `fixture_only: false` and
`generative_enabled: false` values. Sources cannot become exportable through
registration: they still require a supported deterministic derivative, an
immutable caption, and the production exact-byte human review ceremony.

## Manifest

`SourceRegistrationManifest.model_json_schema()` supplies the closed schema.
There are no inferred defaults for subject, capture group, split, label or
ancestry. A JSON example follows; the placeholder hash and UUID must be replaced
with independently obtained values for the explicitly authorized file.

```json
{
  "schema_version": "omoide.source-registration/v1",
  "name": "Operator-authorized still selection",
  "subject_id": "subject-explicit-identifier",
  "source_root": "/mounted/source/authorized-selection",
  "store_root": "/separate/curation-store",
  "expected_filesystem_uuid": "filesystem-uuid",
  "expected_mountpoint": "/mounted/source",
  "attestation": {
    "operator_id": "operator-identifier",
    "attestation_id": "authorization-record-identifier",
    "statement": "I authorize this exact selection and attest its stated rights and subject.",
    "evidence_reference": "local-record:authorization-reference",
    "rights_to_curate": true,
    "subject_identity_attested": true
  },
  "files": [{
    "relative_path": "explicit-still.jpg",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "label": "Operator-supplied label",
    "subject_id": "subject-explicit-identifier",
    "group_id": "capture-session-explicit-identifier",
    "split": "train",
    "ancestry": {
      "kind": "original_capture",
      "evidence_reference": "local-record:capture-origin-reference",
      "statement": "This exact file is an original nongenerative capture.",
      "ancestry_complete": true,
      "parent_sha256": []
    }
  }]
}
```

The JSON representation is capped at 256 KiB. Strings and ancestry lists have
their own bounds. Every declared source must match its SHA-256 and the existing
16 MiB per-file read bound. Registration reads only those paths, with no directory
listing, recursive census, media inference, network request, model execution,
threshold change or source write. It does not decode media; supported format and
color checks remain materialization gates. The source and store must already
exist, be absolute, have no symlink components, and be disjoint. Bind-mount
aliases of ancestor/descendant roots also fail. No output directory is created
by registration.

The operator's rights, identity, capture group, split and ancestry statements are
stored as **operator attestations**, with evidence references and the exact
manifest digest. They are not authenticated human review decisions or
independently verified facts. An `original_capture` has no declared parents. A
`non_generative_derivative` requires explicit parent hashes and a complete
ancestry attestation; its referenced evidence must account for every ancestor,
including ancestors outside this manifest. The service does not invent ancestry
from filenames, old flags or EXIF. Unknown, incomplete, contradictory or cyclic
declared ancestry and all generative ancestry are rejected, including on excluded
items. This is a trust boundary with the local operator; software cannot verify
the truthfulness of an operator's supplied evidence. Repeated source hashes and
capture groups cannot cross train/validation/test splits; export also retains
the existing pixel-duplicate and holdout checks.

## Observed mounted volume

`observe_source_volume()` asks Linux `findmnt` for structured JSON containing the
observed filesystem UUID, mount ID, mounted target, filesystem root, filesystem
type, device identity and source. It independently checks the mount fields
against `/proc/self/mountinfo`, checks `/proc/self/ns/mnt` identity before and
after observation, and compares the source root's opened descriptor mount ID
from `/proc/self/fdinfo`. The supplied expected UUID and mountpoint are only
constraints compared with these observations. They never substitute for them.

The persisted dataset policy pins those mount fields and namespace identity;
the dataset also pins source-root and store-root device/inode identities. On
filesystems whose Linux drivers do not keep inode numbers across cache eviction
or a remount (`exfat`, `vfat`, `msdos`; they assign numbers with `iunique()`),
registration records `policy.root_identity.source_inode_persistent = false` from
the observed filesystem type and the source-root inode is not compared; the
UUID, filesystem root, mountpoint, device, pinned relative paths and per-file
SHA-256 checks carry that fence instead. The record must agree with the pinned
filesystem type, so it cannot relax the fence elsewhere, and the store-root
inode is always compared. Each source path is verified before and after reads. `read_at` invokes
`verify_source_descriptor(dataset, fd)` before and after byte capture. This
detects nested mounts and same-device bind mounts that a device-only check would
miss, as well as mount substitutions while a descriptor is held. Existing
`O_NOFOLLOW`, bounded regular-file reads, entry/stat comparisons, repeated root
walks and SHA-256 checks remain active.

Btrfs can report different device numbers in a subvolume's `fstat().st_dev` and
the mountinfo filesystem's `major:minor`. A test first reproduced this rejection;
the guard uses the kernel mount ID to bind the descriptor to mountinfo and checks
its actual `st_dev` against the separately registered source root device. It does
not equate these two Btrfs device number representations. A bounded observation
of a task-created temporary directory verified UUID discovery and descriptor
mount identity on the workstation's Btrfs filesystem. It inspected no library
file and is not a production source registration proof.

Missing `findmnt`, timeout, unavailable UUID, wrong UUID, absent mount, conflicting
observations, namespace change, root substitution or nested mount all fail
closed. There is no privileged probe, remount, recursive fallback, caller-UUID
override or relaxed production mode. Mount/namespace changes can require new
explicit registration after operational review; records are not silently rebound.

The fixture bypass requires both policy versions to be `fixture-stills-v1`, exact
`fixture_only: true`, and exact `generative_enabled: false`. A production policy
with a caller-supplied fixture flag cannot bypass observation. Only trusted local
fixture setup may create such fixture policies; HTTP cannot replace a dataset's
policy.

### Docker boundary

A Docker bind mount does not automatically expose the host's filesystem UUID or
the host mount namespace. A host UUID written into a manifest is insufficient
inside the container. Production source reads fail closed if that execution
namespace cannot independently establish the source's UUID and mount identity.
Host-registered namespace/mount records are intentionally not portable to an
unverified container namespace. Source mounts should be read-only at the OS or
container boundary as defense in depth; the application always opens sources
read-only but does not alter mount options or file permissions.

Deployment must provide a reviewed, verifiable execution/storage arrangement
before using production source access. This change does not add a privileged
container, mount the host's devices/proc tree, weaken the gate, change Docker
configuration or deploy a service. A successful health page or browsable bind
mount alone is not filesystem-identity proof.

## Read-only legacy plan

`plan_legacy_dataset_items(session, dataset_id=..., item_ids=[...])` reads metadata
for 1–100 exact unique positive DatasetItem IDs in that dataset. It uses a clean
session with autoflush disabled and makes no database or media-filesystem writes.
There is no implicit all-items selection and no media file access. Missing or
cross-dataset IDs reject the entire selection.

Each item has a source-path proposal, unknown ancestry, no observed source hash,
no capture group or split, a legacy subject reference and explicit blockers.
`accepted` and `registerable` are always false. HUMAN author labels, approved
annotation states and legacy review timestamps remain labeled legacy metadata;
none becomes production review authority. Old edits and non-media origins add a
provenance blocker.

Caption proposals preserve raw UTF-8 text exactly, including whitespace, with
its SHA-256 and exact source field/row/revision. An override takes precedence;
otherwise an annotation-mode dataset gets its latest raw caption observation.
This is explicitly labeled as an **independent proposal**, not the existing
dataset's effective rendered caption or its approved-first selection. The plan
includes the legacy caption source, template, trigger word and class token.
Caption source `none` suppresses all caption proposals; `template` does not pull
in annotations. Annotation content/provenance digests anchor the proposal while
avoiding a copy of arbitrary provenance payloads. Malformed/nontext captions and
unsupported text lengths become blockers, not accepted captions.

The plan is deliberately a different schema from registration. To register any
proposed item, an operator must independently authorize the exact source path,
observe its bytes/hash and volume, and supply complete nongenerative ancestry,
subject, group and split attestations. A separate immutable caption proposal and
human review are still required afterward.

## Integration and verification

The production runtime must call `verify_source_volume(dataset, source.relative_path)`
before and after source access and call `verify_source_descriptor(dataset, fd)`
around every original-byte descriptor read, including readbacks. These hooks are
wired into `curation_artifacts.source_bytes` and its optional `read_at` descriptor
guard. Artifact/store-only reads retain their existing store identity checks.

Focused tests use temporary SQLite databases and rights-clear geometric files,
with isolated XDG directories and simulated kernel/findmnt faults. They exercise
registration rollback, no grant issuance, source immutability, UUID/mount/root/
namespace changes, Btrfs behavior, bind aliases, symlinks, bounds, source hashes,
ancestry/split restrictions and read-only legacy semantics. They do not prove
real-world provenance, real operator enrollment or private-library suitability.
