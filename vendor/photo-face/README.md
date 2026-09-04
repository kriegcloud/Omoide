# Vendored AdaFace runtime

This directory contains the fixed CVLFace inference graph used by Omoide's
isolated face-recognition service. It was vendored from the local
`@beep/repo-cli files match-person` implementation rather than depending on
another checkout at runtime.

The recognizer is `cvlface_adaface_vit_base_kprpe_webface12m`. The code graph
tracks CVLFace revision `308142aa50adf2e187711354f7524635d3414f1e`; model
artifact revisions, expected byte sizes, and SHA-256 hashes are declared in
`beep_photo_face/backends/adaface_kprpe.py`. Model artifacts are deliberately
not committed.

The user service receives encoded image bytes through a private Unix socket.
It does not accept filesystem paths and is denied access to the configured T7
mounts by the workstation systemd unit.

CVLFace code is MIT licensed; see
`beep_photo_face/backends/UPSTREAM-CVLFACE-LICENSE.txt`. Checkpoint use is
also subject to its upstream model-card and training-dataset terms. InsightFace
pretrained detector weights have separate licensing terms.
