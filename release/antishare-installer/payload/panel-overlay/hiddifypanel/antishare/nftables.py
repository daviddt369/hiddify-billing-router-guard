from __future__ import annotations

import subprocess

from loguru import logger


class NftBanBackend:
    def __init__(self, *, helper_path: str, enabled: bool, dry_run: bool) -> None:
        self.helper_path = helper_path
        self.enabled = enabled
        self.dry_run = dry_run

    def ensure(self) -> None:
        if not self.enabled:
            return
        self._run("ensure")

    def ban_ips(self, ips: list[str], duration_seconds: int, user_label: str) -> None:
        if not self.enabled:
            logger.info("Anti-share: nft backend disabled, skipping bans for {}", user_label)
            return
        for ip in ips:
            self._run("ban", ip, str(duration_seconds), user_label)

    def unban_ip(self, ip: str) -> None:
        if not self.enabled:
            logger.info("Anti-share: nft backend disabled, skipping unban for {}", ip)
            return
        self._run("unban", ip)

    def _run(self, *args: str) -> None:
        cmd = ["sudo", "-n", self.helper_path, *args]
        if self.dry_run:
            logger.info("Anti-share dry-run: {}", " ".join(cmd))
            return
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            logger.error("Anti-share nft helper timed out (30s): {}", " ".join(cmd))
        except subprocess.CalledProcessError as exc:
            logger.error("Anti-share nft helper failed: {} :: {}", " ".join(cmd), (exc.stderr or exc.stdout or "").strip())
