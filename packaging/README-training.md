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

Load and enable the watcher:

```sh
systemctl --user daemon-reload
systemctl --user enable --now omoide-train.path
```

Inspect launcher activity with:

```sh
journalctl --user -u omoide-train.service
```
