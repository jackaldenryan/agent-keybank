---
name: keybank
description: >
  Use this skill when the user wants to pick, load, add, list, or run with an
  API key, token, credential, or secret from KeyBank. Trigger on "use the
  development key", "use the production key", "load the API key", "which API
  keys do I have", "add a new API key", "put the key in .env", or any request
  to inject credentials into a script, folder, or command. Discover keys with
  `keybank list` (ids and descriptions only). Load with `keybank load` or
  `keybank run`. Never open secrets.env, never open a generated .env, and
  never print secret values.
license: MIT
compatibility: Requires Python 3.9+ and the keybank CLI on PATH
metadata:
  version: "0.1.0"
---

# KeyBank

Machine-wide API key bank. Catalog is safe to read. Secrets are not.

Bank: `$KEYBANK_HOME` or `~/.keybank`
Safe file: `catalog.yaml` (or `keybank list` / `keybank show`)
Forbidden: `secrets.env`, any generated `.env`

If `keybank` is missing, tell the user to install the CLI and run `keybank setup`. Do not invent a bank path.

```
pipx install agent-keybank
```

```
keybank setup
```

## Hard rules

- Discover keys with `keybank list --json` or `keybank show <query> --json`.
- Match a request with `keybank resolve "<what they said>"` or pass that phrase straight to `load` / `run`.
- Load into a file with `keybank load <id-or-query> --into <dir-or-env-file>`.
- Set the key for one command with `keybank run <id-or-query> -- <command>`. Prefer this when a leftover `.env` is not needed.
- Never read `secrets.env`. Never read a generated `.env`. Never `cat`, `open`, or print env values.
- Never put a secret on a command line.
- If the match is ambiguous or missing, show the catalog and ask. Do not guess.

## Pick and load a key

1. `keybank list --json`
2. Match the user's words to an `id`, alias, or description.
3. If they named no key, list the catalog and ask.
4. Create the script/folder so it reads the **runtime** name (`maps_to`), not the catalog id. Several keys can share one runtime name, such as `SERVICE_API_KEY`.
5. Load into a file, or inject for one command:

```bash
keybank load development --into ./scripts/my-probe/.env
```

```bash
keybank load service-dev platform-work --into ./scripts/my-probe/.env
```

```bash
keybank load service-dev --as OTHER_API_KEY --into ./scripts/my-probe/.env
```

```bash
keybank run development -- python script.py
```

6. Stop. Do not open a `.env` you just wrote.

`--into DIR` writes `DIR/.env`. `--into FILE` writes that file. `--as VAR` renames the secret for a single key; `--as ID=VAR` works with several. `--as` does not rename `public` companions.

## Add a key

Write the catalog entry only. Do not touch the secret.

```bash
keybank add service-dev \
  --description "Development environment" \
  --alias dev --alias development \
  --maps-to SERVICE_API_KEY \
  --public SERVICE_API_URL=https://api.dev.example.com
```

Then tell the user to paste the secret into `~/.keybank/secrets.env` as:

```
service-dev=<secret>
```

Never open that file to confirm. If they ask you to store a secret they already pasted in chat, tell them to paste it into `secrets.env` themselves.

Update metadata with `keybank add <id> --update ...`. Remove with `keybank remove <query> --yes`.

## Public companions

`public` values (URLs, non-secret config) are safe. Use them from `keybank show` instead of opening `.env`.

## Ambiguous or missing

`keybank resolve` / `load` / `run` exit non-zero when the query matches nothing or several keys. Print that error, list the catalog, and ask which `id` to use.
