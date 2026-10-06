# /access bot token and actor header Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The owner-only `/access` cog authenticates to the axi-config admin API with the dedicated bot token (`AXI_CONFIG_BOT_TOKEN`) and tells the API which Discord user acted, through an `X-Axi-Actor` header instead of a `createdBy` body field.

**Architecture:** Only `axitools/cogs/access_admin.py` changes. `setup()` reads `AXI_CONFIG_BOT_TOKEN` in place of `AXI_CONFIG_ADMIN_TOKEN`. `AdminApi._request` gains an optional `actor` argument that adds `X-Axi-Actor: discord:<id>`; `ban` and `unban` pass it, `list_bans` does not. The `createdBy` body field is dropped, because the Worker now derives `created_by` from the authenticated actor and ignores a client-supplied value.

**Tech Stack:** Python 3.11+, discord.py ≥ 2.3 (app_commands), aiohttp, pytest + pytest-asyncio + aioresponses.

**Spec:** `darkharasho/axi-config`, file `docs/superpowers/specs/2026-10-06-axiadmin-design.md`, §5 "axitools `/access` cog" and the axitools line of §6 "Testing". The spec repo is private; the rules this plan needs are copied into Global Constraints.

## Global Constraints

- The loader requires `AXI_CONFIG_BOT_TOKEN` in place of `AXI_CONFIG_ADMIN_TOKEN`. It still also requires `AXI_ADMIN_GUILD_ID` and `AXI_OWNER_ID`. If any of the three is missing, the cog is not loaded. `AXI_CONFIG_ADMIN_TOKEN` is no longer read at all.
- Registration only in the admin guild, the `interaction.user.id == AXI_OWNER_ID` check, and ephemeral replies are all unchanged.
- `AdminApi` sends `X-Axi-Actor: discord:<interaction.user.id>` on `ban` and `unban`, and drops `createdBy` from the body. `list_bans` sends no `X-Axi-Actor` header.
- Every admin request still sends `Authorization: Bearer <token>`, where the token is now the value of `AXI_CONFIG_BOT_TOKEN`.
- Admin API shapes are otherwise unchanged:
  - `POST /v1/admin/bans {kind, value, reason}` → `{ban, created}`
  - `DELETE /v1/admin/bans/{id}` → `{ban, changed}`
  - `GET /v1/admin/bans` → `{bans}`
  - Errors are `{error: <code>}`.
- The Worker accepts the header only in the form `discord:<digits>`; anything else is silently ignored (actor stays `bot`). `interaction.user.id` is always an int, so the value is always digits.
- Never print, log or commit a token value. `.env.example` lists the variable name with an empty value only.
- Work on a new branch `feat/access-bot-token` created from `main`.
- Tests: `PYTHONPATH=. pytest tests`. Commits use Conventional Commits and end with a blank line then `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Only the old variable is set** (venus before its `.env` is updated, or a stale checkout): the cog must not load, and the log line must name `AXI_CONFIG_BOT_TOKEN` so the operator knows what to add. Pinned by `test_setup_ignores_the_old_admin_token` in Task 1.
2. **`/access list` must not claim an actor.** Reads are not audited; sending the header on `GET` would be harmless but violates the spec line. Pinned by `test_list_sends_no_actor_header` in Task 1.
3. **`/access restore` must carry the actor**, otherwise the audit row for `ban.remove` records plain `bot` instead of the operator. Pinned by `test_restore_sends_the_actor_header` in Task 1.
4. **`revoke-user` (the user picker) must send the operator's id, not the picked user's id**, as the actor. The picked id belongs only in `value`. Pinned by `test_revoke_user_actor_is_the_operator_not_the_target` in Task 1.
5. **A whitespace-only `AXI_CONFIG_BOT_TOKEN`** (e.g. `AXI_CONFIG_BOT_TOKEN= ` in `.env`) must count as missing, as the old variable did, so the cog does not load and fire requests with `Bearer ` and an empty token. Pinned by `test_setup_treats_blank_bot_token_as_missing` in Task 1.

## File Structure

- Modify `axitools/cogs/access_admin.py`: module docstring, `AdminApi._request`, `AdminApi.ban`, `AdminApi.unban`, `AccessAdminCog._ban`, `AccessAdminCog.restore`, `setup`.
- Modify `tests/test_cogs_access_admin.py`: update the revoke and setup tests, add the actor-header and loader tests.
- Modify `.env.example`: rename the variable.

No other file in the repo mentions `AXI_CONFIG_ADMIN_TOKEN` except the historical plan `docs/superpowers/plans/2026-10-05-access-policy.md`, which is left as written.

---

### Task 1: Bot token loader and X-Axi-Actor header

**Files:**
- Modify: `axitools/cogs/access_admin.py` (docstring lines 1-9, `AdminApi` lines 42-74, `_ban` lines 111-118, `restore` lines 141-149, `setup` lines 179-192)
- Modify: `.env.example:29-33`
- Test: `tests/test_cogs_access_admin.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces:
  - `AdminApi._request(self, method: str, path: str, body: dict | None = None, actor: int | str | None = None) -> dict[str, Any]` — adds header `X-Axi-Actor: discord:<actor>` when `actor` is not `None`.
  - `AdminApi.ban(self, kind: str, value: str, reason: str | None, actor_id: int | str) -> dict[str, Any]` — body `{"kind", "value", "reason"}` only.
  - `AdminApi.unban(self, ban_id: str, actor_id: int | str) -> dict[str, Any]`
  - `AdminApi.list_bans(self) -> dict[str, Any]` — unchanged, no actor.
  - `setup(bot)` reads `AXI_CONFIG_BOT_TOKEN`.

- [ ] **Step 1: Create the branch**

```bash
cd /var/home/mstephens/Documents/GitHub/axitools
git switch main
git pull --ff-only
git switch -c feat/access-bot-token
```

- [ ] **Step 2: Update the existing tests to the new contract**

In `tests/test_cogs_access_admin.py`, replace `test_revoke_posts_to_the_admin_api` with:

```python
@pytest.mark.asyncio
async def test_revoke_posts_to_the_admin_api():
    cog = _cog()
    interaction = _interaction()
    with aioresponses() as m:
        m.post(f"{BASE}/v1/admin/bans", payload={"ban": BAN, "created": True}, status=201)
        await cog.revoke.callback(cog, interaction, app_commands.Choice(name="GW2 account", value="gw2_account"), "Name.1234", "spam")
        request = list(m.requests.values())[0][0]
        assert request.kwargs["json"] == {"kind": "gw2_account", "value": "Name.1234", "reason": "spam"}
        assert "createdBy" not in request.kwargs["json"]
        assert request.kwargs["headers"]["Authorization"] == "Bearer " + "t" * 40
        assert request.kwargs["headers"]["X-Axi-Actor"] == f"discord:{OWNER}"
    text = interaction.followup.send.await_args.args[0]
    assert text.startswith("Revoked")
    assert "b_abcdefghij" in text
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
```

Replace `test_setup_skips_without_configuration` with:

```python
@pytest.mark.asyncio
async def test_setup_skips_without_configuration(monkeypatch):
    for name in ("AXI_ADMIN_GUILD_ID", "AXI_OWNER_ID", "AXI_CONFIG_BOT_TOKEN", "AXI_CONFIG_ADMIN_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    bot = MagicMock()
    bot.add_cog = AsyncMock()
    await access_admin.setup(bot)
    bot.add_cog.assert_not_awaited()
```

Replace `test_setup_registers_only_in_the_admin_guild` with:

```python
@pytest.mark.asyncio
async def test_setup_registers_only_in_the_admin_guild(monkeypatch):
    monkeypatch.setenv("AXI_ADMIN_GUILD_ID", "444444444444444444")
    monkeypatch.setenv("AXI_OWNER_ID", str(OWNER))
    monkeypatch.setenv("AXI_CONFIG_BOT_TOKEN", "b" * 40)
    monkeypatch.delenv("AXI_CONFIG_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("AXI_CONFIG_URL", raising=False)
    bot = MagicMock()
    bot.add_cog = AsyncMock()
    await access_admin.setup(bot)
    cog = bot.add_cog.await_args.args[0]
    assert bot.add_cog.await_args.kwargs["guild"] == discord.Object(id=444444444444444444)
    assert cog.owner_id == OWNER
    assert cog.api.base_url == "https://config.axi.link"
    assert cog.api._token == "b" * 40
```

- [ ] **Step 3: Add the new tests**

Append to `tests/test_cogs_access_admin.py`:

```python
@pytest.mark.asyncio
async def test_restore_sends_the_actor_header():
    cog = _cog()
    interaction = _interaction()
    with aioresponses() as m:
        m.delete(f"{BASE}/v1/admin/bans/b_abcdefghij", payload={"ban": {**BAN, "revokedAt": "x"}, "changed": True})
        await cog.restore.callback(cog, interaction, "b_abcdefghij")
        request = list(m.requests.values())[0][0]
        assert request.kwargs["headers"]["X-Axi-Actor"] == f"discord:{OWNER}"
        assert request.kwargs["headers"]["Authorization"] == "Bearer " + "t" * 40
        assert request.kwargs.get("json") is None
    assert interaction.followup.send.await_args.args[0].startswith("Restored")


@pytest.mark.asyncio
async def test_list_sends_no_actor_header():
    cog = _cog()
    interaction = _interaction()
    with aioresponses() as m:
        m.get(f"{BASE}/v1/admin/bans", payload={"bans": []})
        await cog.list_cmd.callback(cog, interaction)
        request = list(m.requests.values())[0][0]
        assert "X-Axi-Actor" not in request.kwargs["headers"]
        assert request.kwargs["headers"]["Authorization"] == "Bearer " + "t" * 40
    assert interaction.followup.send.await_args.args[0] == "No active bans."


@pytest.mark.asyncio
async def test_revoke_user_actor_is_the_operator_not_the_target():
    cog = _cog()
    user = MagicMock(spec=discord.User)
    user.id = 333333333333333333
    with aioresponses() as m:
        m.post(f"{BASE}/v1/admin/bans", payload={"ban": {**BAN, "kind": "discord_user", "value": "333333333333333333"}, "created": True}, status=201)
        interaction = _interaction()
        await cog.revoke_user.callback(cog, interaction, user, None)
        request = list(m.requests.values())[0][0]
        assert request.kwargs["json"] == {"kind": "discord_user", "value": "333333333333333333", "reason": None}
        assert request.kwargs["headers"]["X-Axi-Actor"] == f"discord:{OWNER}"


@pytest.mark.asyncio
async def test_setup_ignores_the_old_admin_token(monkeypatch, caplog):
    monkeypatch.setenv("AXI_ADMIN_GUILD_ID", "444444444444444444")
    monkeypatch.setenv("AXI_OWNER_ID", str(OWNER))
    monkeypatch.setenv("AXI_CONFIG_ADMIN_TOKEN", "t" * 40)
    monkeypatch.delenv("AXI_CONFIG_BOT_TOKEN", raising=False)
    bot = MagicMock()
    bot.add_cog = AsyncMock()
    with caplog.at_level("INFO", logger=access_admin.LOGGER.name):
        await access_admin.setup(bot)
    bot.add_cog.assert_not_awaited()
    assert "AXI_CONFIG_BOT_TOKEN" in caplog.text
    assert "t" * 40 not in caplog.text


@pytest.mark.asyncio
async def test_setup_treats_blank_bot_token_as_missing(monkeypatch):
    monkeypatch.setenv("AXI_ADMIN_GUILD_ID", "444444444444444444")
    monkeypatch.setenv("AXI_OWNER_ID", str(OWNER))
    monkeypatch.setenv("AXI_CONFIG_BOT_TOKEN", "   ")
    monkeypatch.delenv("AXI_CONFIG_ADMIN_TOKEN", raising=False)
    bot = MagicMock()
    bot.add_cog = AsyncMock()
    await access_admin.setup(bot)
    bot.add_cog.assert_not_awaited()
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/test_cogs_access_admin.py -v`
Expected: FAIL in
- `test_revoke_posts_to_the_admin_api` (body still contains `createdBy`)
- `test_restore_sends_the_actor_header` (`KeyError: 'X-Axi-Actor'`)
- `test_revoke_user_actor_is_the_operator_not_the_target` (body contains `createdBy`)
- `test_setup_registers_only_in_the_admin_guild` (`add_cog` never awaited, so `await_args` is `None`)
- `test_setup_ignores_the_old_admin_token` (cog loaded from the old variable)

`test_list_sends_no_actor_header`, `test_setup_skips_without_configuration` and `test_setup_treats_blank_bot_token_as_missing` already pass; the rest stay green.

- [ ] **Step 5: Implement the AdminApi change**

In `axitools/cogs/access_admin.py`, replace the whole `AdminApi` class with:

```python
class AdminApi:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token

    async def _request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        actor: int | str | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._token}"}
        if actor is not None:
            headers["X-Axi-Actor"] = f"discord:{actor}"
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method,
                self.base_url + path,
                json=body,
                headers=headers,
            ) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = None
                if resp.status >= 400:
                    code = data.get("error") if isinstance(data, dict) else None
                    raise AdminApiError(code if isinstance(code, str) else f"HTTP {resp.status}")
                return data if isinstance(data, dict) else {}

    async def ban(self, kind: str, value: str, reason: str | None, actor_id: int | str) -> dict[str, Any]:
        return await self._request(
            "POST", "/v1/admin/bans", {"kind": kind, "value": value, "reason": reason}, actor=actor_id
        )

    async def unban(self, ban_id: str, actor_id: int | str) -> dict[str, Any]:
        return await self._request("DELETE", f"/v1/admin/bans/{quote(ban_id, safe='')}", actor=actor_id)

    async def list_bans(self) -> dict[str, Any]:
        return await self._request("GET", "/v1/admin/bans")
```

- [ ] **Step 6: Pass the operator id from the cog**

In `AccessAdminCog._ban`, replace

```python
        result = await self._call(
            interaction, "Revoke", self.api.ban(kind, value, reason, str(interaction.user.id))
        )
```

with

```python
        result = await self._call(
            interaction, "Revoke", self.api.ban(kind, value, reason, interaction.user.id)
        )
```

In `AccessAdminCog.restore`, replace

```python
        result = await self._call(interaction, "Restore", self.api.unban(ban_id))
```

with

```python
        result = await self._call(interaction, "Restore", self.api.unban(ban_id, interaction.user.id))
```

- [ ] **Step 7: Switch the loader to the bot token**

Replace the first five lines of the body of `setup` (the three `os.getenv` lines, the `if`, and the `LOGGER.info`/`return`) with:

```python
    guild_id = os.getenv("AXI_ADMIN_GUILD_ID", "").strip()
    owner_id = os.getenv("AXI_OWNER_ID", "").strip()
    token = os.getenv("AXI_CONFIG_BOT_TOKEN", "").strip()
    if not (guild_id.isdigit() and owner_id.isdigit() and token):
        LOGGER.info("access_admin not loaded: AXI_ADMIN_GUILD_ID, AXI_OWNER_ID and AXI_CONFIG_BOT_TOKEN are required")
        return
```

Replace the module docstring with:

```python
"""Owner-only /access commands: revoke and restore access to the Axi apps.

Access can be revoked for people, GW2 guilds and Discord servers that violate
the Axi apps' terms of use (see README, "Access"). These commands call the
axi-config admin API with the dedicated bot token, and send the operator's
Discord id as `X-Axi-Actor: discord:<id>` on every write so the API's audit log
records who acted. They are registered only in the author's private admin
server (AXI_ADMIN_GUILD_ID), answer only AXI_OWNER_ID, and reply ephemerally,
because the list holds personal identifiers and reasons. The cog is not loaded
unless AXI_ADMIN_GUILD_ID, AXI_OWNER_ID and AXI_CONFIG_BOT_TOKEN are all set.
"""
```

- [ ] **Step 8: Update `.env.example`**

Replace lines 29-33 of `.env.example`:

```
# Owner-only /access command (axitools/cogs/access_admin.py). Loaded only when all three are set.
AXI_ADMIN_GUILD_ID=
AXI_OWNER_ID=
AXI_CONFIG_ADMIN_TOKEN=
```

with:

```
# Owner-only /access command (axitools/cogs/access_admin.py). Loaded only when all three are set.
# AXI_CONFIG_BOT_TOKEN is the axi-config Worker's BOT_TOKEN secret (not the old admin token).
AXI_ADMIN_GUILD_ID=
AXI_OWNER_ID=
AXI_CONFIG_BOT_TOKEN=
```

- [ ] **Step 9: Run the cog tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_cogs_access_admin.py -v`
Expected: all PASS.

- [ ] **Step 10: Check nothing else still references the old variable or `createdBy` as a request field**

Run: `grep -rn "AXI_CONFIG_ADMIN_TOKEN\|createdBy" --exclude-dir=.git --exclude-dir=.venv --exclude-dir=docs .`
Expected: only `tests/test_cogs_access_admin.py` lines that delete or set `AXI_CONFIG_ADMIN_TOKEN` in the setup tests, the `BAN` fixture's `"createdBy"` (that is response data, not a request field), and the `assert "createdBy" not in ...` line.

- [ ] **Step 11: Run the full suite**

Run: `PYTHONPATH=. pytest tests`
Expected: all PASS.

- [ ] **Step 12: Commit**

```bash
git add axitools/cogs/access_admin.py tests/test_cogs_access_admin.py .env.example
git commit -m "feat(access): use the axi-config bot token and send X-Axi-Actor

The /access cog now loads with AXI_CONFIG_BOT_TOKEN instead of
AXI_CONFIG_ADMIN_TOKEN, sends X-Axi-Actor: discord:<id> on ban and
unban so the admin API audits the operator, and no longer sends
createdBy in the ban body.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Self-review

- Spec §5 loader line → Steps 2, 3, 7 (`AXI_CONFIG_BOT_TOKEN` required; guild and owner id still required).
- Spec §5 unchanged behaviour (admin-guild registration, owner check, ephemeral) → existing tests `test_setup_registers_only_in_the_admin_guild`, `test_non_owner_is_refused_and_api_is_not_called`, `test_revoke_posts_to_the_admin_api` (ephemeral) stay and pass.
- Spec §5 header on ban and unban, `createdBy` dropped → Steps 2, 3, 5, 6.
- Spec §6 axitools line (loader needs the new var; header sent; `createdBy` absent) → `test_setup_registers_only_in_the_admin_guild`, `test_setup_ignores_the_old_admin_token`, `test_revoke_posts_to_the_admin_api`, `test_restore_sends_the_actor_header`.
- Names: `actor_id` in `ban`/`unban`, `actor` in `_request` — used consistently in Steps 5 and 6.
- Review Focus items 1-5 each have a named test in Step 3.

## Appendix: Rollout notes

These are not plan tasks. Deploying is done by the controller together with the author, after the axi-config Worker has the `BOT_TOKEN` secret (spec §7 step 1).

- On venus, add `AXI_CONFIG_BOT_TOKEN` to `~/axitools/.env` with the same value as the Worker's `BOT_TOKEN` secret. Write it without echoing it (for example with an editor, or by piping from a password manager); never print it in a terminal, log or commit.
- Keep `AXI_CONFIG_ADMIN_TOKEN` in `~/axitools/.env` until axi-config retires the legacy `ADMIN_TOKEN` path (spec §7 step 5). The new code ignores it, so it is harmless, and it keeps a rollback to the previous axitools build working.
- After deploy, verify in the admin server that `/access list` works, and that a test revoke and restore are recorded in the axi-config audit log with actor `bot:discord:<owner id>`.
- If the cog is missing after deploy, the bot log line `access_admin not loaded: ... AXI_CONFIG_BOT_TOKEN are required` means the variable is absent or blank in `.env`.
