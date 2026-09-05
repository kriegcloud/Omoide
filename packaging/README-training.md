# Omoide host training launcher

Omoide creates run requests inside its host data directory. A systemd user
path unit notices each `REQUESTED` file and invokes ai-toolkit on the host,
where ROCm and the Radeon AI PRO R9700 are available.

## Install

```sh
install -Dm755 packaging/omoide-train-launcher \
  ~/.local/bin/omoide-train-launcher
install -Dm644 packaging/systemd/omoide-train.path \
  ~/.config/systemd/user/omoide-train.path
install -Dm644 packaging/systemd/omoide-train.service \
  ~/.config/systemd/user/omoide-train.service
test -e ~/.config/omoide/train-launcher.env || \
  install -Dm600 /dev/null ~/.config/omoide/train-launcher.env
```

Edit `~/.config/omoide/train-launcher.env`:

```sh
AI_TOOLKIT_DIR=/home/elpresidank/YeeBois/dev/ai-toolkit
AI_TOOLKIT_PYTHON=/absolute/path/to/a/python-with-ai-toolkit-dependencies
# Required only for the gated FLUX.1-dev preset:
# HF_TOKEN=hf_...
# Optional, only when this ROCm installation needs an explicit gfx override:
# HSA_OVERRIDE_GFX_VERSION=...
```

`AI_TOOLKIT_DIR` must contain `run.py`; `AI_TOOLKIT_PYTHON` must be an
executable Python with ai-toolkit's dependencies installed. The launcher does
not set CUDA environment variables. It exports `HSA_OVERRIDE_GFX_VERSION` only
when that line is present in the environment file.

The workstation deployment stores datasets under
`~/.local/share/omoide-portal/datasets`. If the host data directory is moved,
set `OMOIDE_DATASETS_ROOT` in the environment file and update
`PathExistsGlob` and `ReadWritePaths` in the two units to the same absolute
path.

## Base-model presets

The Runs tab offers three ai-toolkit presets: Z-Image (the workstation
default), Z-Image Turbo, and gated FLUX.1-dev. Z-Image and Z-Image Turbo use
their public Hugging Face model ids unless a local single-file model path is
configured. FLUX.1-dev requires a non-empty `HF_TOKEN` in
`train-launcher.env`; the launcher reports only whether the token is present
and never writes its value to the heartbeat.

The workstation container accepts these environment variables:

- `OMOIDE_TRAINING__DEFAULT_BASE_MODEL`: default preset id (`z-image`,
  `z-image-turbo`, or `flux-dev`).
- `OMOIDE_TRAINING__Z_IMAGE_PATH`: opaque host path to a bf16 Z-Image
  `.safetensors` file. An empty value uses `Tongyi-MAI/Z-Image`.
- `OMOIDE_TRAINING__Z_IMAGE_TURBO_PATH`: opaque host path to a bf16 Z-Image
  Turbo `.safetensors` file. An empty value uses
  `Tongyi-MAI/Z-Image-Turbo`.
- `OMOIDE_TRAINING__LAUNCHER_STALE_AFTER_SECONDS`: age after which the
  launcher heartbeat is considered stale (default 120 seconds).

These paths belong to the host and are intentionally not checked by the
container.

Load and enable the watcher:

```sh
systemctl --user daemon-reload
systemctl --user enable --now omoide-train.path
```

Inspect launcher activity with:

```sh
journalctl --user -u omoide-train.service
```

## Timer fallback

systemd path units do not reliably notice `REQUESTED` files created inside
freshly created nested directories, so `omoide-train.timer` also runs the
launcher every 30 seconds. Enable both:

```bash
systemctl --user enable --now omoide-train.path omoide-train.timer
```

Every invocation atomically updates
`<OMOIDE_DATASETS_ROOT>/.launcher/heartbeat.json`, including invocations that
stop early because ai-toolkit is misconfigured. The heartbeat contains its
timestamp, hostname, launcher version, ai-toolkit path, toolkit readiness,
and a boolean indicating whether `HF_TOKEN` is configured. It never contains
the token itself. The Runs tab warns when this heartbeat is missing or stale.
