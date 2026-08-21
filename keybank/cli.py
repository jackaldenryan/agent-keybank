#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

__version__ = "0.1.1"

ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SIMPLE_SCALAR_RE = re.compile(r"^[A-Za-z0-9_./:@%+=,-]+$")
SAFE_ENV_VALUE_RE = re.compile(r"^[A-Za-z0-9_./:@%+=,-]+$")


class KeybankError(Exception):
    pass


class YamlError(KeybankError):
    def __init__(self, message: str, line: Optional[int] = None) -> None:
        self.line = line
        if line is not None:
            super().__init__(f"catalog.yaml:{line}: {message}")
        else:
            super().__init__(message)


@dataclass
class KeyEntry:
    id: str
    description: str
    notes: str = ""
    aliases: list[str] = field(default_factory=list)
    maps_to: str = ""
    public: dict[str, str] = field(default_factory=dict)

    def to_public_dict(self, has_secret: bool) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "notes": self.notes,
            "aliases": list(self.aliases),
            "maps_to": self.maps_to,
            "public": dict(self.public),
            "has_secret": has_secret,
        }


def bank_home() -> Path:
    raw = os.environ.get("KEYBANK_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".keybank"


def catalog_path() -> Path:
    return bank_home() / "catalog.yaml"


def secrets_path() -> Path:
    return bank_home() / "secrets.env"


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def fail(message: str, code: int = 1) -> int:
    eprint(f"Error: {message}")
    return code


def atomic_write(path: Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".keybank-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            if not content.endswith("\n"):
                handle.write("\n")
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def ensure_mode(path: Path, mode: int) -> None:
    if path.exists():
        os.chmod(path, mode)


def _strip_comment(line: str) -> str:
    in_single = False
    in_double = False
    for index, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            return line[:index].rstrip()
    return line.rstrip()


def _unescape(inner: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(inner):
        if inner[index] == "\\" and index + 1 < len(inner):
            out.append(inner[index + 1])
            index += 2
            continue
        out.append(inner[index])
        index += 1
    return "".join(out)


def _parse_scalar(raw: str) -> str:
    text = raw.strip()
    if text in {"~", "null", "Null", "NULL"}:
        return ""
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return _unescape(text[1:-1])
    return text


def _split_flow_items(inner: str) -> list[str]:
    items: list[str] = []
    buf: list[str] = []
    in_single = False
    in_double = False
    for char in inner:
        if char == "'" and not in_double:
            in_single = not in_single
            buf.append(char)
        elif char == '"' and not in_single:
            in_double = not in_double
            buf.append(char)
        elif char == "," and not in_single and not in_double:
            item = "".join(buf).strip()
            if item:
                items.append(item)
            buf = []
        else:
            buf.append(char)
    tail = "".join(buf).strip()
    if tail:
        items.append(tail)
    return items


def _parse_flow_list(raw: str, line_no: int) -> list[str]:
    text = raw.strip()
    if not (text.startswith("[") and text.endswith("]")):
        raise YamlError("expected a [list]", line_no)
    return [_parse_scalar(item) for item in _split_flow_items(text[1:-1])]


def parse_simple_yaml(text: str) -> object:
    prepared: list[tuple[int, int, str]] = []
    for line_no, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw:
            raise YamlError("tabs are not allowed; use spaces", line_no)
        stripped = _strip_comment(raw)
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        prepared.append((line_no, indent, stripped.strip()))

    pos = 0

    def peek() -> Optional[tuple[int, int, str]]:
        if pos >= len(prepared):
            return None
        return prepared[pos]

    def parse_value(min_indent: int) -> object:
        nonlocal pos
        current = peek()
        if current is None:
            return ""
        line_no, indent, content = current
        if indent < min_indent:
            return ""
        if content.startswith("- "):
            return parse_list(indent)
        if content == "-":
            return parse_list(indent)
        if content.endswith(":") or (": " in content and not content.startswith("[")):
            return parse_map(indent)
        pos += 1
        if content.startswith("[") and content.endswith("]"):
            return _parse_flow_list(content, line_no)
        return _parse_scalar(content)

    def parse_map(map_indent: int) -> dict:
        nonlocal pos
        result: dict = {}
        while True:
            current = peek()
            if current is None:
                break
            line_no, indent, content = current
            if indent < map_indent:
                break
            if indent > map_indent:
                raise YamlError("unexpected indent", line_no)
            if content.startswith("-"):
                break
            pos += 1
            if content.endswith(":"):
                key = _parse_scalar(content[:-1])
                nxt = peek()
                if nxt is None or nxt[1] <= indent:
                    if key == "aliases":
                        result[key] = []
                    elif key == "public":
                        result[key] = {}
                    else:
                        result[key] = ""
                elif nxt[2].startswith("-"):
                    result[key] = parse_list(nxt[1])
                else:
                    result[key] = parse_value(indent + 1)
            elif ": " in content:
                key_raw, value_raw = content.split(": ", 1)
                key = _parse_scalar(key_raw)
                value_raw = value_raw.strip()
                if value_raw.startswith("[") and value_raw.endswith("]"):
                    result[key] = _parse_flow_list(value_raw, line_no)
                else:
                    result[key] = _parse_scalar(value_raw)
            else:
                raise YamlError(f"expected key: value, got {content!r}", line_no)
        return result

    def parse_list(list_indent: int) -> list:
        nonlocal pos
        result: list = []
        while True:
            current = peek()
            if current is None:
                break
            line_no, indent, content = current
            if indent < list_indent:
                break
            if indent > list_indent:
                raise YamlError("unexpected indent", line_no)
            if not content.startswith("-"):
                break
            pos += 1
            rest = content[1:].strip()
            if not rest:
                nxt = peek()
                if nxt is None or nxt[1] <= indent:
                    result.append("")
                else:
                    result.append(parse_value(indent + 1))
            elif rest.endswith(":") or ": " in rest:
                pos -= 1
                prepared[pos] = (line_no, indent + 2, rest)
                result.append(parse_map(indent + 2))
            elif rest.startswith("[") and rest.endswith("]"):
                result.append(_parse_flow_list(rest, line_no))
            else:
                result.append(_parse_scalar(rest))
        return result

    if not prepared:
        return {}
    first_line, first_indent, first_content = prepared[0]
    if first_content.startswith("-"):
        return parse_list(first_indent)
    if first_content.endswith(":") or ": " in first_content:
        return parse_map(first_indent)
    raise YamlError("catalog must be a mapping with a top-level keys: list", first_line)


def yaml_quote(value: str) -> str:
    if value == "":
        return '""'
    if SIMPLE_SCALAR_RE.fullmatch(value) and value not in {"null", "true", "false", "~"}:
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def dump_catalog(keys: list[KeyEntry]) -> str:
    lines = ["keys:"]
    if not keys:
        lines.append("  []")
        lines.append("")
        return "\n".join(lines)
    for key in keys:
        lines.append(f"  - id: {yaml_quote(key.id)}")
        lines.append(f"    description: {yaml_quote(key.description)}")
        if key.notes:
            lines.append(f"    notes: {yaml_quote(key.notes)}")
        if key.aliases:
            aliases = ", ".join(yaml_quote(alias) for alias in key.aliases)
            lines.append(f"    aliases: [{aliases}]")
        if key.maps_to:
            lines.append(f"    maps_to: {yaml_quote(key.maps_to)}")
        if key.public:
            lines.append("    public:")
            for pub_key, pub_val in key.public.items():
                lines.append(f"      {yaml_quote(pub_key)}: {yaml_quote(pub_val)}")
    lines.append("")
    return "\n".join(lines)


def _as_str_list(value: object, field_name: str, key_id: str) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise KeybankError(f"{key_id}: {field_name} entries must be strings")
            item = item.strip()
            if item:
                out.append(item)
        return out
    if isinstance(value, str):
        return [value] if value.strip() else []
    raise KeybankError(f"{key_id}: {field_name} must be a list of strings")


def _as_str_map(value: object, field_name: str, key_id: str) -> dict[str, str]:
    if value is None or value == "":
        return {}
    if not isinstance(value, dict):
        raise KeybankError(f"{key_id}: {field_name} must be a mapping of strings")
    out: dict[str, str] = {}
    for map_key, map_val in value.items():
        if not isinstance(map_key, str) or not isinstance(map_val, str):
            raise KeybankError(f"{key_id}: {field_name} keys and values must be strings")
        if map_key.strip():
            out[map_key.strip()] = map_val
    return out


def entries_from_data(data: object) -> list[KeyEntry]:
    if not isinstance(data, dict):
        raise KeybankError("catalog.yaml must be a mapping with a top-level keys: list")
    raw_keys = data.get("keys", [])
    if raw_keys is None or raw_keys == "":
        raw_keys = []
    if not isinstance(raw_keys, list):
        raise KeybankError("catalog.yaml keys: must be a list")
    entries: list[KeyEntry] = []
    seen: set[str] = set()
    for item in raw_keys:
        if not isinstance(item, dict):
            raise KeybankError("each catalog entry must be a mapping")
        key_id = str(item.get("id", "")).strip()
        if not key_id:
            raise KeybankError("catalog entry is missing id")
        validate_id(key_id)
        if key_id in seen:
            raise KeybankError(f"duplicate catalog id: {key_id}")
        seen.add(key_id)
        description = item.get("description", "")
        if not isinstance(description, str):
            raise KeybankError(f"{key_id}: description must be a string")
        notes = item.get("notes", "")
        if not isinstance(notes, str):
            raise KeybankError(f"{key_id}: notes must be a string")
        maps_to = item.get("maps_to", "")
        if not isinstance(maps_to, str):
            raise KeybankError(f"{key_id}: maps_to must be a string")
        maps_to = maps_to.strip()
        if maps_to:
            validate_env_name(maps_to)
        public = _as_str_map(item.get("public"), "public", key_id)
        for pub_key in public:
            validate_env_name(pub_key)
        entries.append(
            KeyEntry(
                id=key_id,
                description=description.strip(),
                notes=notes.strip(),
                aliases=_as_str_list(item.get("aliases"), "aliases", key_id),
                maps_to=maps_to,
                public=public,
            )
        )
    return entries


def load_catalog(path: Optional[Path] = None) -> list[KeyEntry]:
    target = path or catalog_path()
    if not target.is_file():
        raise KeybankError(
            f"no catalog at {target}. Run `keybank setup` first."
        )
    text = target.read_text(encoding="utf-8")
    return entries_from_data(parse_simple_yaml(text))


def save_catalog(keys: list[KeyEntry], path: Optional[Path] = None) -> None:
    target = path or catalog_path()
    atomic_write(target, dump_catalog(keys), 0o644)


def parse_env_file(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = _unescape(value[1:-1])
        if key:
            out[key] = value
    return out


def format_env_value(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise KeybankError("secret values cannot contain newlines")
    if SAFE_ENV_VALUE_RE.fullmatch(value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def dump_env_file(values: dict[str, str], header: Optional[str] = None) -> str:
    lines: list[str] = []
    if header:
        lines.append(header)
    for key, value in values.items():
        lines.append(f"{key}={format_env_value(value)}")
    lines.append("")
    return "\n".join(lines)


def load_secrets(path: Optional[Path] = None) -> dict[str, str]:
    target = path or secrets_path()
    if not target.is_file():
        return {}
    return parse_env_file(target.read_text(encoding="utf-8"))


def save_secrets(secrets: dict[str, str], path: Optional[Path] = None) -> None:
    target = path or secrets_path()
    atomic_write(target, dump_env_file(secrets), 0o600)


def validate_id(key_id: str) -> None:
    if not ID_RE.fullmatch(key_id):
        raise KeybankError(
            f"invalid id {key_id!r}. Use lowercase letters, numbers, and single hyphens."
        )


def validate_env_name(name: str) -> None:
    if not ENV_NAME_RE.fullmatch(name):
        raise KeybankError(f"invalid environment variable name {name!r}")


def parse_public_pair(raw: str) -> tuple[str, str]:
    if "=" not in raw:
        raise KeybankError(f"--public expects NAME=value, got {raw!r}")
    name, value = raw.split("=", 1)
    name = name.strip()
    validate_env_name(name)
    return name, value


def parse_as_args(raw_items: list[str], keys: list[KeyEntry]) -> dict[str, str]:
    remaps: dict[str, str] = {}
    if not raw_items:
        return remaps
    if len(raw_items) == 1 and "=" not in raw_items[0]:
        if len(keys) != 1:
            raise KeybankError("--as VAR can only be used when loading a single key")
        validate_env_name(raw_items[0])
        remaps[keys[0].id] = raw_items[0]
        return remaps
    known = {key.id for key in keys}
    for item in raw_items:
        if "=" not in item:
            raise KeybankError(
                "--as must be VAR (one key) or ID=VAR (any number of keys)"
            )
        key_id, name = item.split("=", 1)
        key_id = key_id.strip()
        name = name.strip()
        if key_id not in known:
            raise KeybankError(f"--as refers to unknown selected id {key_id!r}")
        validate_env_name(name)
        remaps[key_id] = name
    return remaps


def resolve(keys: list[KeyEntry], query: str) -> list[KeyEntry]:
    needle = query.strip()
    if not needle:
        return []
    lowered = needle.lower()

    exact_id = [key for key in keys if key.id.lower() == lowered]
    if exact_id:
        return exact_id

    exact_alias = [
        key for key in keys if lowered in {alias.lower() for alias in key.aliases}
    ]
    if exact_alias:
        return exact_alias

    prefix = [key for key in keys if key.id.lower().startswith(lowered)]
    if len(prefix) == 1:
        return prefix

    words = [word for word in re.split(r"\W+", lowered) if word]
    if not words:
        return prefix
    matched: list[KeyEntry] = []
    for key in keys:
        haystack = " ".join([key.id, " ".join(key.aliases), key.description]).lower()
        if all(word in haystack for word in words):
            matched.append(key)
    return matched


def require_one(keys: list[KeyEntry], query: str) -> KeyEntry:
    matches = resolve(keys, query)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise KeybankError(
            f"no key matched {query!r}. Run `keybank list` to see available keys."
        )
    listing = "\n".join(
        f"  {key.id:<20} {key.description}" for key in matches
    )
    raise KeybankError(f"{query!r} is ambiguous. Matches:\n{listing}\nSay which one to use.")


def resolve_many(keys: list[KeyEntry], queries: list[str]) -> list[KeyEntry]:
    selected: list[KeyEntry] = []
    seen: set[str] = set()
    for query in queries:
        entry = require_one(keys, query)
        if entry.id in seen:
            continue
        seen.add(entry.id)
        selected.append(entry)
    return selected


def materialize(
    selected: list[KeyEntry],
    secrets: dict[str, str],
    remaps: Optional[dict[str, str]] = None,
) -> dict[str, str]:
    remaps = remaps or {}
    env: dict[str, str] = {}
    for key in selected:
        secret = secrets.get(key.id)
        if secret is None or secret == "":
            raise KeybankError(
                f"{key.id} has no secret. Set it with: keybank set-secret {key.id}"
            )
        dest = remaps.get(key.id) or key.maps_to
        if not dest:
            raise KeybankError(
                f"{key.id} has no maps_to. Pass --as VAR or add maps_to in the catalog."
            )
        if dest in env:
            raise KeybankError(
                f"two keys both write {dest}. Use --as to rename one of them."
            )
        env[dest] = secret
        for pub_key, pub_val in key.public.items():
            if pub_key in env and env[pub_key] != pub_val:
                raise KeybankError(
                    f"public variable {pub_key} conflicts across selected keys"
                )
            env[pub_key] = pub_val
    return env


def format_table(keys: list[KeyEntry], secrets: dict[str, str]) -> str:
    if not keys:
        return "No keys in the catalog. Add one with `keybank add`."
    rows = [["ID", "MAPS TO", "SECRET", "ALIASES", "DESCRIPTION"]]
    for key in keys:
        rows.append(
            [
                key.id,
                key.maps_to or "-",
                "yes" if secrets.get(key.id) else "no",
                ", ".join(key.aliases) if key.aliases else "-",
                key.description or "-",
            ]
        )
    widths = [max(len(row[col]) for row in rows) for col in range(len(rows[0]))]
    lines: list[str] = []
    for index, row in enumerate(rows):
        line = "  ".join(cell.ljust(widths[col]) for col, cell in enumerate(row))
        lines.append(line.rstrip())
        if index == 0:
            lines.append("  ".join("-" * width for width in widths))
    return "\n".join(lines)


def require_bank() -> None:
    home = bank_home()
    if not home.is_dir() or not catalog_path().is_file():
        raise KeybankError(
            f"no keybank at {home}. Run `keybank setup` first."
        )


def cmd_init(_args: argparse.Namespace) -> int:
    home = bank_home()
    home.mkdir(mode=0o700, exist_ok=True)
    os.chmod(home, 0o700)
    created: list[str] = []
    if not catalog_path().exists():
        save_catalog([])
        created.append(str(catalog_path()))
    else:
        ensure_mode(catalog_path(), 0o644)
    if not secrets_path().exists():
        save_secrets({})
        created.append(str(secrets_path()))
    else:
        ensure_mode(secrets_path(), 0o600)
    if created:
        print(f"Initialized keybank at {home}")
        for path in created:
            print(f"  created {path}")
    else:
        print(f"Keybank already exists at {home}")
    print("Agents may read catalog.yaml. Agents must never open secrets.env.")
    return 0


def cmd_home(_args: argparse.Namespace) -> int:
    print(bank_home())
    return 0


def cmd_catalog_path(_args: argparse.Namespace) -> int:
    print(catalog_path())
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    require_bank()
    keys = load_catalog()
    secrets = load_secrets()
    if args.json:
        payload = [key.to_public_dict(bool(secrets.get(key.id))) for key in keys]
        print(json.dumps(payload, indent=2))
        return 0
    print(format_table(keys, secrets))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    require_bank()
    keys = load_catalog()
    secrets = load_secrets()
    entry = require_one(keys, args.query)
    payload = entry.to_public_dict(bool(secrets.get(entry.id)))
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    print(f"id:          {entry.id}")
    print(f"description: {entry.description or '-'}")
    print(f"notes:       {entry.notes or '-'}")
    print(f"aliases:     {', '.join(entry.aliases) if entry.aliases else '-'}")
    print(f"maps_to:     {entry.maps_to or '-'}")
    print(f"has_secret:  {'yes' if payload['has_secret'] else 'no'}")
    if entry.public:
        print("public:")
        for pub_key, pub_val in entry.public.items():
            print(f"  {pub_key}={pub_val}")
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    require_bank()
    keys = load_catalog()
    matches = resolve(keys, args.query)
    if args.json:
        print(json.dumps([key.id for key in matches], indent=2))
        if len(matches) == 1:
            return 0
        return 2 if matches else 1
    if not matches:
        return fail(f"no key matched {args.query!r}. Run `keybank list` to see available keys.")
    if len(matches) > 1:
        listing = "\n".join(f"  {key.id:<20} {key.description}" for key in matches)
        return fail(f"{args.query!r} is ambiguous. Matches:\n{listing}\nSay which one to use.", 2)
    print(matches[0].id)
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    require_bank()
    validate_id(args.id)
    keys = load_catalog()
    existing = next((key for key in keys if key.id == args.id), None)
    if existing and not args.update:
        raise KeybankError(f"{args.id} already exists. Pass --update to change it.")
    if not args.description and (existing is None or not existing.description):
        raise KeybankError("--description is required")
    public = dict(existing.public) if existing else {}
    for pair in args.public:
        name, value = parse_public_pair(pair)
        public[name] = value
    aliases = list(args.alias)
    if existing and not aliases and not args.replace_aliases:
        aliases = list(existing.aliases)
    maps_to = args.maps_to or (existing.maps_to if existing else "")
    if maps_to:
        validate_env_name(maps_to)
    description = args.description if args.description is not None else existing.description
    notes = args.notes if args.notes is not None else (existing.notes if existing else "")
    entry = KeyEntry(
        id=args.id,
        description=description,
        notes=notes,
        aliases=aliases,
        maps_to=maps_to,
        public=public,
    )
    if existing:
        keys = [entry if key.id == args.id else key for key in keys]
        action = "Updated"
    else:
        keys.append(entry)
        action = "Added"
    save_catalog(keys)
    print(f"{action} {args.id}.")
    secrets = load_secrets()
    if not secrets.get(args.id):
        print(f"Paste the secret into {secrets_path()} as:")
        print(f"  {args.id}=<secret>")
    return 0


def cmd_set_secret(args: argparse.Namespace) -> int:
    require_bank()
    keys = load_catalog()
    entry = require_one(keys, args.query)
    if sys.stdin.isatty():
        import getpass

        secret = getpass.getpass("Secret (input hidden): ")
    else:
        secret = sys.stdin.read()
        if secret.endswith("\n"):
            secret = secret[:-1]
    if not secret:
        raise KeybankError("secret is empty")
    if "\n" in secret or "\r" in secret:
        raise KeybankError("secret values cannot contain newlines")
    secrets = load_secrets()
    secrets[entry.id] = secret
    save_secrets(secrets)
    print(f"Saved secret for {entry.id}.")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    require_bank()
    keys = load_catalog()
    entry = require_one(keys, args.query)
    if not args.yes:
        raise KeybankError(f"pass --yes to remove {entry.id}")
    save_catalog([key for key in keys if key.id != entry.id])
    secrets = load_secrets()
    if entry.id in secrets:
        del secrets[entry.id]
        save_secrets(secrets)
    print(f"Removed {entry.id}.")
    return 0


def resolve_into_path(raw: str) -> Path:
    target = Path(raw).expanduser()
    if target.exists() and target.is_dir():
        return target / ".env"
    if raw.endswith(os.sep) or raw.endswith("/"):
        return target / ".env"
    if not target.suffix and not target.exists():
        return target / ".env"
    return target


def cmd_load(args: argparse.Namespace) -> int:
    require_bank()
    keys = load_catalog()
    selected = resolve_many(keys, args.queries)
    remaps = parse_as_args(args.as_name, selected)
    secrets = load_secrets()
    env = materialize(selected, secrets, remaps)
    dest = resolve_into_path(args.into).resolve()
    if dest == secrets_path().resolve() or dest == catalog_path().resolve():
        raise KeybankError("refusing to write over the keybank catalog or secrets file")
    merged: dict[str, str] = {}
    if dest.is_file():
        merged.update(parse_env_file(dest.read_text(encoding="utf-8")))
    merged.update(env)
    header = "# Generated by keybank. Do not commit. Agents must not open this file."
    atomic_write(dest, dump_env_file(merged, header=header), 0o600)
    names = ", ".join(sorted(env))
    print(f"Loaded {', '.join(key.id for key in selected)} into {dest}")
    print(f"Wrote: {names}")
    print("Do not open that file. It contains secrets.")
    return 0


def dispatch_run(argv: list[str]) -> int:
    if "--" in argv:
        index = argv.index("--")
        left, command = argv[:index], argv[index + 1 :]
    else:
        left, command = argv, []
    parser = argparse.ArgumentParser(prog="keybank run")
    parser.add_argument("queries", nargs="+")
    parser.add_argument("--as", dest="as_name", action="append", default=[])
    args = parser.parse_args(left)
    args.command = command
    return cmd_run(args)


def cmd_run(args: argparse.Namespace) -> int:
    require_bank()
    command = list(args.command)
    if not command:
        raise KeybankError("provide a command after --")
    keys = load_catalog()
    selected = resolve_many(keys, args.queries)
    remaps = parse_as_args(args.as_name, selected)
    secrets = load_secrets()
    injected = materialize(selected, secrets, remaps)
    env = os.environ.copy()
    env.update(injected)
    try:
        os.execvpe(command[0], command, env)
    except FileNotFoundError:
        return fail(f"command not found: {command[0]}", 127)
    return 1


def cmd_doctor(_args: argparse.Namespace) -> int:
    home = bank_home()
    print(f"Bank:     {home}")
    print(f"Catalog:  {catalog_path()}")
    print(f"Secrets:  {secrets_path()}")
    problems = 0
    if not home.is_dir():
        print("Status:   missing (run `keybank setup`)")
        return 1
    mode = stat.S_IMODE(home.stat().st_mode)
    if mode & 0o077:
        print(f"Warning:  bank directory mode is {mode:o}; expected 700")
        problems += 1
    if not catalog_path().is_file():
        print("Status:   catalog.yaml missing")
        return 1
    try:
        keys = load_catalog()
        print(f"Entries:  {len(keys)}")
    except KeybankError as exc:
        print(f"Status:   catalog.yaml is invalid: {exc}")
        return 1
    if secrets_path().is_file():
        sec_mode = stat.S_IMODE(secrets_path().stat().st_mode)
        if sec_mode != 0o600:
            print(f"Warning:  secrets.env mode is {sec_mode:o}; expected 600")
            problems += 1
        secrets = load_secrets()
    else:
        print("Warning:  secrets.env missing")
        secrets = {}
        problems += 1
    for key in keys:
        mark = "set" if secrets.get(key.id) else "MISSING SECRET"
        if not secrets.get(key.id):
            problems += 1
        print(f"  {key.id}: {mark}")
    orphans = [name for name in secrets if name not in {key.id for key in keys}]
    if orphans:
        print(f"Orphans:  {', '.join(orphans)} (in secrets.env, not in catalog)")
        problems += 1
    cli = shutil_which("keybank")
    print(f"CLI:      {cli or 'not on PATH'}")
    if not cli:
        problems += 1
    skill_hits = []
    for rel in skill_md_paths():
        if rel.is_file():
            skill_hits.append(str(rel))
    print(f"Skill:    {', '.join(skill_hits) if skill_hits else 'not installed'}")
    if problems:
        print(f"Doctor:   {problems} issue(s)")
        return 1
    print("Doctor:   ok")
    return 0


@dataclass(frozen=True)
class AgentTarget:
    id: str
    label: str
    dirs: tuple[Path, ...]


def agent_targets() -> tuple[AgentTarget, ...]:
    home = Path.home()
    shared = home / ".agents" / "skills" / "keybank"
    return (
        AgentTarget("claude", "Claude Code", (home / ".claude" / "skills" / "keybank",)),
        AgentTarget(
            "codex",
            "Codex",
            (home / ".codex" / "skills" / "keybank", shared),
        ),
        AgentTarget(
            "cursor",
            "Cursor",
            (home / ".cursor" / "skills" / "keybank", shared),
        ),
        AgentTarget(
            "opencode",
            "OpenCode",
            (home / ".config" / "opencode" / "skills" / "keybank", shared),
        ),
        AgentTarget("gemini", "Gemini CLI", (home / ".gemini" / "skills" / "keybank",)),
        AgentTarget("factory", "Factory", (home / ".factory" / "skills" / "keybank",)),
        AgentTarget("grok", "Grok", (home / ".grok" / "skills" / "keybank",)),
        AgentTarget("amp", "Amp", (home / ".config" / "amp" / "skills" / "keybank",)),
    )


def skill_md_paths() -> tuple[Path, ...]:
    seen: list[Path] = []
    found: set[Path] = set()
    for target in agent_targets():
        for folder in target.dirs:
            path = folder / "SKILL.md"
            if path not in found:
                found.add(path)
                seen.append(path)
    return tuple(seen)


def bundled_skill_path() -> Path:
    path = Path(__file__).resolve().parent / "skill" / "SKILL.md"
    if not path.is_file():
        raise KeybankError("bundled SKILL.md is missing from the keybank package")
    return path


def install_skill_dirs(dirs: list[Path]) -> list[Path]:
    source = bundled_skill_path()
    written: list[Path] = []
    for folder in dirs:
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / "SKILL.md"
        shutil.copy2(source, dest)
        written.append(dest)
    return written


def parse_agent_selection(raw: str) -> list[AgentTarget]:
    targets = {target.id: target for target in agent_targets()}
    text = raw.strip().lower()
    if not text or text in {"all", "*"}:
        return list(agent_targets())
    if text in {"none", "skip"}:
        return []
    selected: list[AgentTarget] = []
    seen: set[str] = set()
    for part in text.split(","):
        agent_id = part.strip()
        if not agent_id:
            continue
        if agent_id not in targets:
            known = ", ".join(target.id for target in agent_targets())
            raise KeybankError(f"unknown agent {agent_id!r}. Choose from: all, none, {known}")
        if agent_id not in seen:
            seen.add(agent_id)
            selected.append(targets[agent_id])
    return selected


def prompt_agent_selection() -> list[AgentTarget]:
    targets = list(agent_targets())
    print("Which coding agents should get the KeyBank skill?")
    print()
    for index, target in enumerate(targets, 1):
        print(f"  {index}) {target.label}")
    print(f"  {len(targets) + 1}) All of them")
    print("  0) None — CLI and bank only")
    print()
    raw = input(f"Enter numbers, comma-separated [{len(targets) + 1}]: ").strip()
    if not raw or raw == str(len(targets) + 1):
        return targets
    if raw == "0":
        return []
    chosen: list[AgentTarget] = []
    seen: set[str] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            number = int(part)
        except ValueError as exc:
            raise KeybankError(f"invalid choice {part!r}") from exc
        if number == 0:
            return []
        if number == len(targets) + 1:
            return targets
        if number < 1 or number > len(targets):
            raise KeybankError(f"invalid choice {number}")
        target = targets[number - 1]
        if target.id not in seen:
            seen.add(target.id)
            chosen.append(target)
    return chosen


def cmd_setup(args: argparse.Namespace) -> int:
    if args.agents is not None:
        selected = parse_agent_selection(args.agents)
    elif sys.stdin.isatty() and sys.stdout.isatty():
        print("KeyBank setup")
        print()
        print(f"Bank:  {bank_home()}")
        print("Skill: copied into the agent folders you choose")
        print()
        selected = prompt_agent_selection()
    else:
        raise KeybankError(
            "non-interactive setup needs --agents. Try: keybank setup --agents all"
        )
    init_code = cmd_init(args)
    if init_code != 0:
        return init_code
    dest_dirs: list[Path] = []
    seen: set[Path] = set()
    for target in selected:
        for folder in target.dirs:
            if folder not in seen:
                seen.add(folder)
                dest_dirs.append(folder)
    if dest_dirs:
        written = install_skill_dirs(dest_dirs)
        print("Skill installed:")
        for path in written:
            print(f"  {path}")
    else:
        print("Skipped skill install.")
    print()
    print("Tell an agent what secrets you need. It will add ids and descriptions.")
    print(f"Then paste each secret into {secrets_path()}")
    return 0


def shutil_which(name: str) -> Optional[str]:
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        if not folder:
            continue
        candidate = Path(folder) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="keybank",
        description=(
            "Catalog-backed API key loader. Lists descriptions, never prints secrets, "
            "and writes chosen keys into a .env or a child process."
        ),
    )
    parser.add_argument("--version", action="version", version=f"keybank {__version__}")
    sub = parser.add_subparsers(dest="command_name", required=True)

    p_setup = sub.add_parser(
        "setup",
        help="create the bank and install the skill for the agents you use",
    )
    p_setup.add_argument(
        "--agents",
        help="all, none, or a comma-separated list: claude,codex,cursor,opencode,gemini,factory,grok,amp",
    )
    p_setup.set_defaults(func=cmd_setup)

    p_init = sub.add_parser("init", help="create ~/.keybank (or $KEYBANK_HOME)")
    p_init.set_defaults(func=cmd_init)

    p_home = sub.add_parser("home", help="print the bank directory")
    p_home.set_defaults(func=cmd_home)

    p_cat = sub.add_parser("catalog-path", help="print catalog.yaml path (safe to read)")
    p_cat.set_defaults(func=cmd_catalog_path)

    p_list = sub.add_parser("list", help="list keys and descriptions (no secrets)")
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="show one catalog entry (no secret)")
    p_show.add_argument("query")
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=cmd_show)

    p_resolve = sub.add_parser("resolve", help="match a description or alias to an id")
    p_resolve.add_argument("query")
    p_resolve.add_argument("--json", action="store_true")
    p_resolve.set_defaults(func=cmd_resolve)

    p_add = sub.add_parser("add", help="add or update a catalog entry (not the secret)")
    p_add.add_argument("id")
    p_add.add_argument(
        "--description",
        help="when to use this key; agents match against this to pick a key",
    )
    p_add.add_argument(
        "--notes",
        help="how to use this key after it is chosen; not used for matching",
    )
    p_add.add_argument("--alias", action="append", default=[])
    p_add.add_argument("--maps-to", dest="maps_to")
    p_add.add_argument("--public", action="append", default=[], metavar="NAME=value")
    p_add.add_argument("--update", action="store_true")
    p_add.add_argument(
        "--replace-aliases",
        action="store_true",
        help="with --update, replace aliases instead of keeping existing ones",
    )
    p_add.set_defaults(func=cmd_add)

    p_secret = sub.add_parser("set-secret", help="set a secret from stdin (never argv)")
    p_secret.add_argument("query")
    p_secret.set_defaults(func=cmd_set_secret)

    p_rm = sub.add_parser("remove", help="delete a catalog entry and its secret")
    p_rm.add_argument("query")
    p_rm.add_argument("--yes", action="store_true")
    p_rm.set_defaults(func=cmd_remove)

    p_load = sub.add_parser("load", help="write selected keys into a .env file")
    p_load.add_argument("queries", nargs="+")
    p_load.add_argument("--into", required=True, help="directory or .env path")
    p_load.add_argument(
        "--as",
        dest="as_name",
        action="append",
        default=[],
        help="VAR, or ID=VAR",
    )
    p_load.set_defaults(func=cmd_load)

    p_run = sub.add_parser("run", help="run a command with selected keys in the environment")
    p_run.add_argument("queries", nargs="+")
    p_run.add_argument(
        "--as",
        dest="as_name",
        action="append",
        default=[],
        help="VAR, or ID=VAR",
    )
    p_run.add_argument("command", nargs=argparse.REMAINDER)
    p_run.set_defaults(func=cmd_run)

    p_doc = sub.add_parser("doctor", help="check bank layout, permissions, and install")
    p_doc.set_defaults(func=cmd_doctor)
    return parser


def _own_help_requested(argv: list[str]) -> bool:
    own = argv[: argv.index("--")] if "--" in argv else argv
    return "-h" in own or "--help" in own


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv and argv[0] == "run" and not _own_help_requested(argv):
            return dispatch_run(argv[1:])
        parser = build_parser()
        args = parser.parse_args(argv)
        return args.func(args)
    except KeybankError as exc:
        return fail(str(exc))
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
