"""Local GitHub firmware repository catalog for ESP32 Manager."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol
from aiohttp import ClientError
from homeassistant.components import websocket_api
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
STORE_VERSION = 1
STORE_KEY = f"{DOMAIN}_repositories"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MAX_TOKEN_LENGTH = 512


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_repository_url(value: str) -> tuple[str, str]:
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
        raise ValueError("Укажите URL вида https://github.com/owner/repository.")
    path = parsed.path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if not REPO_RE.fullmatch(path):
        raise ValueError("URL должен вести непосредственно на GitHub-репозиторий.")
    return path, f"https://github.com/{path}"


def _public_repo(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "name": item["name"],
        "url": item["url"],
        "has_token": bool(item.get("token")),
        "access_ok": item.get("access_ok"),
        "last_checked": item.get("last_checked"),
        "last_error": item.get("last_error"),
    }


async def async_setup_repositories(hass) -> None:
    if STORE_KEY in hass.data.get(DOMAIN, {}):
        return
    store = Store(hass, STORE_VERSION, STORE_KEY)
    saved = await store.async_load() or {}
    repos = saved.get("repositories", [])
    hass.data.setdefault(DOMAIN, {})[STORE_KEY] = {
        "store": store,
        "repositories": {
            item["id"]: item for item in repos
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        },
    }


async def _save(hass) -> None:
    data = hass.data[DOMAIN][STORE_KEY]
    await data["store"].async_save({
        "repositories": list(data["repositories"].values())
    })


def _get_repo(hass, repo_id: str) -> dict[str, Any]:
    repo = hass.data[DOMAIN][STORE_KEY]["repositories"].get(repo_id)
    if repo is None:
        raise ValueError("Репозиторий не найден в каталоге.")
    return repo


async def _github_json(hass, url: str, token: str = "") -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ESP32-Manager-Home-Assistant",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    session = async_get_clientsession(hass)
    async with session.get(url, headers=headers, timeout=20) as response:
        if response.status == 401:
            raise ValueError("GitHub отклонил token (401 Unauthorized).")
        if response.status == 403:
            raise ValueError("GitHub запретил доступ (403). Проверьте права и лимиты API.")
        if response.status == 404:
            raise ValueError("Репозиторий или релизы не найдены либо недоступны этому token (404).")
        response.raise_for_status()
        return await response.json()


async def _check_repo(hass, repo: dict[str, Any]) -> None:
    owner_repo = repo["id"]
    await _github_json(hass, f"https://api.github.com/repos/{owner_repo}", repo.get("token", ""))
    repo["access_ok"] = True
    repo["last_checked"] = _now()
    repo["last_error"] = None


def _admin(connection) -> bool:
    return bool(connection.user and connection.user.is_admin)


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/repositories/list",
})
@websocket_api.async_response
async def ws_list_repositories(hass, connection, msg):
    if not _admin(connection):
        connection.send_error(msg["id"], "unauthorized", "Administrator access required")
        return
    data = hass.data[DOMAIN][STORE_KEY]
    connection.send_result(msg["id"], [
        _public_repo(item) for item in sorted(
            data["repositories"].values(), key=lambda item: item["name"].casefold()
        )
    ])


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/repositories/save",
    vol.Required("url"): str,
    vol.Required("name"): str,
    vol.Optional("token", default=""): str,
    vol.Optional("clear_token", default=False): bool,
})
@websocket_api.async_response
async def ws_save_repository(hass, connection, msg):
    if not _admin(connection):
        connection.send_error(msg["id"], "unauthorized", "Administrator access required")
        return
    try:
        repo_id, normalized_url = _normalize_repository_url(msg["url"])
        name = msg["name"].strip()
        token = msg["token"].strip()
        if not name or len(name) > 80:
            raise ValueError("Имя репозитория должно содержать от 1 до 80 символов.")
        if len(token) > MAX_TOKEN_LENGTH:
            raise ValueError("GitHub token слишком длинный.")
        repos = hass.data[DOMAIN][STORE_KEY]["repositories"]
        previous = repos.get(repo_id, {})
        item = {
            "id": repo_id,
            "name": name,
            "url": normalized_url,
            "token": "" if msg["clear_token"] else (token or previous.get("token", "")),
            "access_ok": previous.get("access_ok"),
            "last_checked": previous.get("last_checked"),
            "last_error": previous.get("last_error"),
        }
        repos[repo_id] = item
        await _save(hass)
        connection.send_result(msg["id"], _public_repo(item))
    except (ValueError, OSError) as err:
        connection.send_error(msg["id"], "invalid_format", str(err))


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/repositories/remove",
    vol.Required("repo_id"): str,
})
@websocket_api.async_response
async def ws_remove_repository(hass, connection, msg):
    if not _admin(connection):
        connection.send_error(msg["id"], "unauthorized", "Administrator access required")
        return
    repos = hass.data[DOMAIN][STORE_KEY]["repositories"]
    if msg["repo_id"] not in repos:
        connection.send_error(msg["id"], "not_found", "Репозиторий не найден.")
        return
    del repos[msg["repo_id"]]
    await _save(hass)
    connection.send_result(msg["id"], {"removed": True})


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/repositories/test",
    vol.Required("repo_id"): str,
})
@websocket_api.async_response
async def ws_test_repository(hass, connection, msg):
    if not _admin(connection):
        connection.send_error(msg["id"], "unauthorized", "Administrator access required")
        return
    try:
        repo = _get_repo(hass, msg["repo_id"])
        await _check_repo(hass, repo)
        await _save(hass)
        connection.send_result(msg["id"], {"repository": _public_repo(repo), "ok": True})
    except (ValueError, ClientError, TimeoutError) as err:
        repo = hass.data[DOMAIN][STORE_KEY]["repositories"].get(msg["repo_id"])
        if repo is not None:
            repo["access_ok"] = False
            repo["last_checked"] = _now()
            repo["last_error"] = str(err)[:240]
            await _save(hass)
        connection.send_error(msg["id"], "cannot_connect", str(err))


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/repositories/releases",
    vol.Required("repo_id"): str,
})
@websocket_api.async_response
async def ws_repository_releases(hass, connection, msg):
    if not _admin(connection):
        connection.send_error(msg["id"], "unauthorized", "Administrator access required")
        return
    try:
        repo = _get_repo(hass, msg["repo_id"])
        payload = await _github_json(
            hass,
            f"https://api.github.com/repos/{repo['id']}/releases?per_page=100",
            repo.get("token", ""),
        )
        releases = []
        for release in payload:
            if release.get("draft"):
                continue
            assets = [{
                "id": asset.get("id"),
                "name": asset.get("name"),
                "size": asset.get("size"),
                "download_url": asset.get("browser_download_url"),
            } for asset in release.get("assets", []) if str(asset.get("name", "")).lower().endswith(".bin")]
            releases.append({
                "id": release.get("id"),
                "tag_name": release.get("tag_name"),
                "name": release.get("name") or release.get("tag_name"),
                "published_at": release.get("published_at"),
                "prerelease": bool(release.get("prerelease")),
                "assets": assets,
            })
        connection.send_result(msg["id"], releases)
    except (ValueError, ClientError, TimeoutError) as err:
        connection.send_error(msg["id"], "cannot_connect", str(err))


def async_register_websocket_commands(hass) -> None:
    for handler in (
        ws_list_repositories,
        ws_save_repository,
        ws_remove_repository,
        ws_test_repository,
        ws_repository_releases,
    ):
        websocket_api.async_register_command(hass, handler)
