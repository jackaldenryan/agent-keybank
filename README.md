# KeyBank

Tell an agent “use the development key.” It matches that phrase to a catalog entry and either writes the key into a `.env` file or sets it for the command it is about to run.

The same skill can add a new API key from a description. The only thing you paste yourself is the secret, into one file: `~/.keybank/secrets.env`.

## Quick start

1. Install the CLI.

```
uv tool install agent-keybank
```

`pipx install agent-keybank` works too.

2. Run setup once. It creates `~/.keybank` and asks which coding agents you use, then installs the skill for those agents.

```
keybank setup
```

Non-interactive:

```
keybank setup --agents all
```

```
keybank setup --agents claude,codex,cursor
```

3. Tell an agent what secrets you need. It writes the ids and descriptions. You paste each secret into `~/.keybank/secrets.env`. After that, “use the development key” is enough.

## After install

Say something like: “I need a development key and a production key for my API, both exposed as `SERVICE_API_KEY`.”

The agent adds catalog entries with ids, descriptions, and the runtime name. It does not see the secrets.

Open `~/.keybank/secrets.env` and paste the values yourself:

```
service-prod=...
service-dev=...
```

Do not ask the agent to open that file. Agents must never read it.

From then on you can say:

- “Use the development key and put it in this folder’s `.env`.”
- “Run this script with the production key.”
- “Add a staging key that maps to `SERVICE_API_KEY`.”

## Where keys live

The bank is on the machine, not in any git repo. Override the location with `KEYBANK_HOME`.

| File | Who may read it | What it holds |
| --- | --- | --- |
| `~/.keybank/catalog.yaml` | You and agents | Id, description, notes, aliases, runtime name, public companions |
| `~/.keybank/secrets.env` | You and the `keybank` CLI only | The secret for each id |

`catalog.yaml` never contains secrets. `secrets.env` is mode `600` and stores values under the catalog id:

```
service-prod=...
service-dev=...
```

## What setup does

`keybank setup` creates `~/.keybank/` with `catalog.yaml` and `secrets.env` if they are missing, then copies the skill into the agent folders you chose.

`--agents all` covers Claude Code, Codex, Cursor, OpenCode, Gemini, Factory, Grok, and Amp.

## Upgrade

Upgrade the CLI, then re-run setup. Setup overwrites the installed `SKILL.md` files. The bank stays put.

```
uv tool upgrade agent-keybank
```

```
keybank setup --agents all
```

`pipx upgrade agent-keybank` works too.

## Uninstall

Uninstall removes the CLI. It does not delete `~/.keybank`.

```
uv tool uninstall agent-keybank
```

`pipx uninstall agent-keybank` works too.

## Commands

Discover keys. These print ids and descriptions, never secret values.

```
keybank list
```

```
keybank list --json
```

```
keybank show development
```

```
keybank resolve "personal project"
```

Load a key into a folder as the runtime name the process expects.

```
keybank load development --into ./scripts/my-probe/.env
```

```
keybank load service-dev platform-work --into ./scripts/my-probe/.env
```

```
keybank load service-dev --as OTHER_API_KEY --into ./scripts/my-probe/.env
```

`--into DIR` writes `DIR/.env`. `--as VAR` renames the secret for one key.

Run a command with the key in the process environment and skip writing a file.

```
keybank run development -- python script.py
```

Add a catalog entry. Then paste the secret into `~/.keybank/secrets.env`. You can also run `keybank set-secret <id>` and type it at a hidden prompt.

```
keybank add service-dev \
  --description "Development environment" \
  --notes "Load env vars as-is. The client reads SERVICE_API_URL without a path suffix." \
  --alias dev --alias development \
  --maps-to SERVICE_API_KEY \
  --public SERVICE_API_URL=https://api.dev.example.com
```

Other commands: `keybank init`, `keybank remove <id> --yes`, `keybank doctor`, `keybank home`.

## Catalog format

Agents may read this file. Keep secrets out of it.

```yaml
keys:
  - id: service-prod
    description: Production API, personal project
    notes: Load env vars as-is. The client reads SERVICE_API_URL without a path suffix.
    aliases: [prod, production]
    maps_to: SERVICE_API_KEY
    public:
      SERVICE_API_URL: https://api.example.com

  - id: service-dev
    description: Development environment
    notes: Load env vars as-is. The client reads SERVICE_API_URL without a path suffix.
    aliases: [dev, development]
    maps_to: SERVICE_API_KEY
    public:
      SERVICE_API_URL: https://api.dev.example.com
```

`description` is when to use the key. Agents match against it to pick an entry. `notes` is how to use the key after it is chosen. `maps_to` is the environment variable the process should see. That is how two keys both become `SERVICE_API_KEY` at use time. `public` holds non-secret companions such as a base URL.

Prefer letting an agent run `keybank add`. Hand-edits work if you stick to this shape.

## Agent skill

The skill is one file bundled with the CLI. It follows the [Agent Skills](https://agentskills.io) standard. `keybank setup` copies it into the personal skill folders for the agents you pick.

New agent sessions pick it up from those folders. The skill tells the agent to use the CLI and to stay out of secret files.

## Security rules

The CLI is the only process that should open `secrets.env`. It never prints secret values.

Generated `.env` files are mode `600`. Add `.env` to `.gitignore` in the folder you load into.

`keybank run` is the safer option when you do not need a leftover file.

Do not put a master `.env` in a git repo root and do not ask an agent to open it.

## Tests

```
python3 -m unittest discover -s tests -v
```

## License

MIT. See [LICENSE](LICENSE). Anyone can use, copy, modify, and distribute this, including commercially.
