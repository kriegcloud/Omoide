"""Command-line entrypoint for the Omoide ComfyUI bridge."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .config import load_config
from .errors import BridgeError
from .service import BridgeService, BridgeUnixServer, install_signal_handlers


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Omoide allowlisted ComfyUI bridge")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--socket", type=Path)
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def main() -> None:
    arguments = _arguments()
    logging.basicConfig(
        level=getattr(logging, str(arguments.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = load_config(arguments.config)
        if arguments.check_config:
            print(
                json.dumps(
                    {
                        "ok": True,
                        "schema": "omoide-comfy-bridge-check/v1",
                        "profiles": {
                            profile_id: profile.workflow_sha256
                            for profile_id, profile in sorted(config.profiles.items())
                        },
                        "comfy_url": config.comfy_base_url,
                    },
                    separators=(",", ":"),
                )
            )
            return
        if arguments.socket is None:
            raise BridgeError(
                "configuration-error",
                "--socket is required unless --check-config is used",
            )
        server = BridgeUnixServer(BridgeService(config), arguments.socket)
        install_signal_handlers(server)
        server.serve_forever()
    except BridgeError as error:
        logging.error("%s: %s", error.code, error.message)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
