# Trainer preprocessing fixture evidence

The still curation slice publishes reviewed image bytes without re-encoding them. A trainer may subsequently decode, resize and crop those images. Export eligibility does not establish downstream face fidelity or approve arbitrary data-loader transforms.

On 2026-09-16, a CPU-only fixture check exercised the exact selected `BucketsMixin` and `ImageProcessingDTOMixin.load_and_process_image` methods from ai-toolkit revision `b1bf3e4381f7d49572ee012c79b36a91da360466`. Source was retrieved from the pinned [bucket helper](https://github.com/ostris/ai-toolkit/blob/b1bf3e4381f7d49572ee012c79b36a91da360466/toolkit/buckets.py) and [data-loader mixins](https://github.com/ostris/ai-toolkit/blob/b1bf3e4381f7d49572ee012c79b36a91da360466/toolkit/dataloader_mixins.py). File SHA-256 pins are retained with the receipt.

The selected profile uses a 1024² pixel budget, divisibility 64, scale 1, center cropping, buckets enabled, no flips, no augmentations, no control images, no generative processing, and Pillow 12.2.0. The check selects unchanged method ASTs to avoid importing the trainer's GPU/model dependencies; it does not instantiate the full trainer. Tensor normalization, latent caches, training and model outputs were not exercised.

| Original | Resize | Crop `(x,y,width,height)` |
| --- | --- | --- |
| 1008×1792 | 768×1366 | 0,11,768,1344 |
| 810×1440 | 768×1366 | 0,11,768,1344 |
| 973×1297 | 896×1195 | 0,21,896,1152 |
| 720×1280 | 720×1280 | 8,0,704,1280 |
| 755×755 | 768×768 | 0,0,768,768 |
| 1792×1008 | 1366×768 | 11,0,1344,768 |

All six generated geometric fixtures preserved original file hashes, repeated processed-pixel hashes, and the positions of contrasting corner regions. These checks establish bounded geometry/repeatability evidence, not human likeness or face-detail quality. The 755×755 case demonstrates that divisibility rounding can increase a dimension even below the nominal area budget. Do not claim that the pinned trainer never upscales.

Private task evidence resides outside this repository in the operator's AI hub run directory (`runs/implementation/krea2-curation-still-slice/`): `check_trainer_preprocessing.py`, `trainer-source/sources.json`, `trainer-preprocessing-receipt.json`, and generated `trainer-fixtures/`. Reproduce using the Omoide Python environment; no weights or inference runtime are needed. The script requires those exact retained source files and verifies their recorded hashes before execution.

## Wider inputs under `rgb-png-v2`

The fixture geometry evidence above was collected with the v1 transform, whose accepted input set was untagged single-frame RGB/grayscale JPEG/PNG/WebP. The `rgb-png-v2` transform accepts ICC-tagged, 16-bit, palette, fully opaque alpha, CMYK and HEIC/HEIF inputs, one explicitly requested frame of a multi-frame still, and one explicitly requested video presentation timestamp. It does not change the encode: an input that v1 accepted still produces the same artifact bytes, so the measured geometry table stays valid for those artifacts and was not re-derived.

Two consequences for the trainer gate. First, artifacts whose provenance carries a `color` record other than `untagged-assumed-srgb` (an ICC conversion, a 16-bit reduction, a naive CMYK conversion, a palette expansion, a dropped opaque alpha) reached sRGB through a documented but not colour-accuracy-proved path; the `uncertainties` list on each artifact and in each export manifest member says which. Do not treat them as colour-managed inputs when assessing face detail. Second, a video-frame artifact is one decoded frame re-encoded losslessly by Pillow, with the container's reported colour metadata recorded rather than applied; its `decoder` provenance block pins the ffmpeg version, timebase and exact PTS, and a different decoder version is a different cache identity. Neither case has been exercised against the pinned trainer profile: no bucket/resize/crop measurement was repeated for them here.

Production trainer integration remains a later gate: pin the complete runtime and effective dataset configuration, reject unreviewed generative transforms, and assess retained face detail and framing with the specifically authorized cohort. This fixture does not change or activate the existing training campaign.
