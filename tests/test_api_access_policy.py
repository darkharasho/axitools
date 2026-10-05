from pathlib import Path

import pytest
import pytest_asyncio

from axitools.api.server import build_app
from axitools.remote_config import RemoteConfig, hash_identity
from axitools.storage import StorageManager

SERVER = 1100000000000000001
OTHER = 1100000000000000002
GW2_GUILD = "4bbb52aa-d768-4fc6-8ede-c299f2822f0f"


class FakeGuild:
    def __init__(self, guild_id: int, name: str) -> None:
        self.id = guild_id
        self.name = name
        self.roles = []
        self.channels = []

    def get_role(self, role_id):
        return None

    def get_channel(self, channel_id):
        return None


class FakeBot:
    def __init__(self, root: Path) -> None:
        self.storage = StorageManager(root)
        self.guilds = [FakeGuild(SERVER, "Revoked"), FakeGuild(OTHER, "Fine")]
        self.remote_config = RemoteConfig(None)
        self.remote_config.set_denylist([
            hash_identity("discord_server", str(SERVER)),
            hash_identity("gw2_guild", GW2_GUILD),
        ])

    def get_guild(self, guild_id):
        return next((g for g in self.guilds if g.id == guild_id), None)


@pytest.fixture(autouse=True)
def _allow_global_token(monkeypatch):
    monkeypatch.setenv("AXITOOLS_ALLOW_GLOBAL_TOKEN", "1")


@pytest_asyncio.fixture
async def client(aiohttp_client, tmp_path):
    return await aiohttp_client(build_app(FakeBot(tmp_path), token="test-token"))


AUTH = {"Authorization": "Bearer test-token"}


@pytest.mark.asyncio
async def test_revoked_server_gets_403_unavailable(client):
    resp = await client.get(f"/guilds/{SERVER}/config", headers=AUTH)
    assert resp.status == 403
    assert await resp.json() == {"error": "unavailable"}


@pytest.mark.asyncio
async def test_unauthenticated_request_to_a_revoked_server_stays_401(client):
    resp = await client.get(f"/guilds/{SERVER}/config")
    assert resp.status == 401


@pytest.mark.asyncio
async def test_other_servers_are_served(client):
    resp = await client.get(f"/guilds/{OTHER}/config", headers=AUTH)
    assert resp.status == 200


@pytest.mark.asyncio
async def test_guild_role_mapping_refuses_a_revoked_gw2_guild(client):
    resp = await client.put(f"/guilds/{OTHER}/guild-roles/{GW2_GUILD}", headers=AUTH, json={"role_id": "1"})
    assert resp.status == 403
    assert await resp.json() == {"error": "unavailable"}


@pytest.mark.asyncio
async def test_alliance_refuses_a_revoked_gw2_guild(client):
    resp = await client.put(f"/guilds/{OTHER}/alliance", headers=AUTH, json={"guild_id": GW2_GUILD.upper()})
    assert resp.status == 403
    assert await resp.json() == {"error": "unavailable"}
