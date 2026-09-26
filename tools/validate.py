"""Validate the repository's published skill and documentation contracts.

This deliberately uses the standard library. Skill frontmatter is a small, flat
subset of YAML; accepting a larger subset here would hide malformed metadata.
"""

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlsplit


FIELDS = {"name", "description"}
LINK = re.compile(r"!?\[[^\]\n]*\]\(\s*(<[^>]+>|[^\s)]+)(?:\s+[^)]*)?\)")
REFERENCE = re.compile(r"^\s*\[[^\]]+\]:\s*(<[^>]+>|\S+)")
HEADING = re.compile(r"^ {0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
VERSION = re.compile(r"\d+\.\d+\.\d+")


def read(path, errors, root):
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        errors.append(f"{path.relative_to(root)}: cannot read: {exc}")
        return None


def scalar(value, path, line, errors):
    if not value:
        errors.append(f"{path}:{line}: empty frontmatter value")
        return None
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            errors.append(f"{path}:{line}: invalid double-quoted string")
            return None
        if not isinstance(parsed, str):
            errors.append(f"{path}:{line}: frontmatter value must be a string")
            return None
        return parsed
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            errors.append(f"{path}:{line}: invalid single-quoted string")
            return None
        inner = value[1:-1]
        if re.search(r"(?<!')'(?!')", inner):
            errors.append(f"{path}:{line}: single quote must be doubled")
            return None
        return inner.replace("''", "'")
    if re.search(r":\s", value) or value.endswith(":"):
        errors.append(f"{path}:{line}: quote a value containing colon-space")
        return None
    if value.startswith(("[", "{", "|", ">", "-", "#")) or value in ("null", "true", "false"):
        errors.append(f"{path}:{line}: expected a flat string")
        return None
    return value


def frontmatter(root, relative, errors):
    source = read(root / relative, errors, root)
    if source is None:
        return {}
    lines = source.splitlines()
    if not lines or lines[0] != "---":
        errors.append(f"{relative}: frontmatter must start on line 1")
        return {}
    try:
        end = lines.index("---", 1)
    except ValueError:
        errors.append(f"{relative}: missing frontmatter closing ---")
        return {}
    fields = {}
    for number, line in enumerate(lines[1:end], 2):
        match = re.fullmatch(r"([A-Za-z][\w-]*):[ \t]*(.*)", line)
        if not match:
            errors.append(f"{relative}:{number}: expected flat key: value")
            continue
        key, raw = match.groups()
        if key not in FIELDS:
            errors.append(f"{relative}:{number}: unexpected frontmatter key {key!r}")
        if key in fields:
            errors.append(f"{relative}:{number}: duplicate frontmatter key {key!r}")
        value = scalar(raw, relative, number, errors)
        if value is not None and not value.strip():
            errors.append(f"{relative}:{number}: empty frontmatter value")
        if value is not None and key == "name" and (len(value) > 64 or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value)):
            errors.append(f"{relative}:{number}: name must be kebab-case and at most 64 characters")
        if value is not None and key == "description" and (len(value) > 1024 or "\n" in value or "\r" in value):
            errors.append(f"{relative}:{number}: description must be one line and at most 1024 characters")
        fields[key] = value
    for key in sorted(FIELDS - fields.keys()):
        errors.append(f"{relative}: missing frontmatter key {key!r}")
    return fields


def metadata(root, errors, skill_name):
    files = [root / ".claude-plugin" / "plugin.json", root / ".claude-plugin" / "marketplace.json"]
    objects = []
    for path in files:
        source = read(path, errors, root)
        if source is None:
            return
        try:
            objects.append(json.loads(source))
        except json.JSONDecodeError as exc:
            errors.append(f"{path.relative_to(root)}: invalid JSON: {exc}")
            return
    plugin, market = objects
    entries = market.get("plugins") if isinstance(market, dict) else None
    if not isinstance(plugin, dict) or not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
        errors.append(".claude-plugin: expected one plugin object in marketplace.json")
        return
    entry = entries[0]
    for label, obj in (("plugin.json", plugin), ("marketplace.json", market), ("marketplace plugin", entry)):
        if obj.get("name") != skill_name:
            errors.append(f".claude-plugin/{label}: name differs from SKILL.md")
    versions = [plugin.get("version"), entry.get("version")]
    if not all(isinstance(v, str) and VERSION.fullmatch(v) for v in versions) or versions[0] != versions[1]:
        errors.append(".claude-plugin: plugin and marketplace versions must match as semantic versions")
        return
    changelog = read(root / "CHANGELOG.md", errors, root)
    if changelog is None:
        return
    releases = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.MULTILINE)
    if not releases or releases[0] != versions[0]:
        errors.append(f"CHANGELOG.md: latest released version must be {versions[0]} (Unreleased may precede it)")


def visible_lines(source, strip_inline_code=True):
    fence_char = None
    fence_length = 0
    for number, line in enumerate(source.splitlines(), 1):
        fence = FENCE.match(line)
        if fence_char:
            if fence and fence.group(1)[0] == fence_char and len(fence.group(1)) >= fence_length and not line[fence.end():].strip():
                fence_char = None
            continue
        if fence:
            fence_char, fence_length = fence.group(1)[0], len(fence.group(1))
            continue
        # Keep inline code text in headings: GitHub includes it in anchor slugs.
        if strip_inline_code:
            line = re.sub(r"(`+)(.*?)\1", "", line)
        yield number, line


def slug(value):
    value = re.sub(r"<[^>]*>", "", value)
    value = re.sub(r"!?\[([^\]]+)\]\([^)]*\)", r"\1", value)
    value = value.lower()
    value = "".join(ch for ch in value if ch in "-_ " or ch.isalnum() or unicodedata.category(ch).startswith("M"))
    return value.replace(" ", "-")


def anchors(source):
    found = set()
    counts = {}
    for _, line in visible_lines(source, strip_inline_code=False):
        match = HEADING.match(line)
        if match:
            base = slug(match.group(1))
            index = counts.get(base, 0)
            found.add(f"{base}-{index}" if index else base)
            counts[base] = index + 1
    return found


def links(root, errors):
    for path in sorted(root.rglob("*.md")):
        if ".git" in path.parts:
            continue
        source = read(path, errors, root)
        if source is None:
            continue
        relative = path.relative_to(root)
        for number, line in visible_lines(source):
            targets = [match.group(1) for match in LINK.finditer(line)]
            reference = REFERENCE.match(line)
            if reference:
                targets.append(reference.group(1))
            for raw in targets:
                target = raw[1:-1] if raw.startswith("<") and raw.endswith(">") else raw
                try:
                    parsed = urlsplit(target)
                except ValueError as exc:
                    errors.append(f"{relative}:{number}: malformed link target: {target} ({exc})")
                    continue
                if parsed.scheme or parsed.netloc or target.startswith(("/", "//")):
                    continue
                linked = (path.parent / unquote(parsed.path)).resolve() if parsed.path else path
                try:
                    linked.relative_to(root.resolve())
                except ValueError:
                    errors.append(f"{relative}:{number}: link escapes repository: {target}")
                    continue
                if not linked.exists():
                    errors.append(f"{relative}:{number}: missing link target: {target}")
                    continue
                if parsed.fragment and linked.is_file() and linked.suffix.lower() == ".md":
                    destination = read(linked, errors, root)
                    if destination is not None and unquote(parsed.fragment) not in anchors(destination):
                        errors.append(f"{relative}:{number}: missing anchor: {target}")


def mirrors(root, errors):
    for directory in ("lanes", "ops"):
        for path in sorted((root / directory).glob("*.md")):
            relative = path.relative_to(root)
            if not (root / "en" / relative).is_file():
                errors.append(f"en/{relative}: missing English mirror")


def validate(root):
    root = Path(root).resolve()
    errors = []
    ko = frontmatter(root, "SKILL.md", errors)
    en = frontmatter(root, "en/SKILL.md", errors)
    if ko.get("name") and en.get("name") and ko["name"] != en["name"]:
        errors.append("en/SKILL.md: name differs from SKILL.md")
    metadata(root, errors, ko.get("name"))
    links(root, errors)
    mirrors(root, errors)
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="repository root")
    args = parser.parse_args(argv)
    errors = validate(args.root)
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        print(f"Validation failed: {len(errors)} error(s)", file=sys.stderr)
        return 1
    print("Repository validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
