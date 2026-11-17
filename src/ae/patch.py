"""Reviewable design patches with a base-hash conflict check (value/MPN edits of components.yaml).

A patch names the file hash it was written against. If the file changed since (another patch, a manual edit),
applying is REFUSED with PatchConflict: the author must rebase. Applying returns the new file hash so the
caller can invalidate dependent evidence. Edits that do not match the recorded `from` value are also refused.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from ae.evidence import sha256_path


class PatchConflict(RuntimeError):
    pass


def apply_patch(patch_path: Path, repo_root: Path, *, dry_run: bool = False) -> dict:
    patch = yaml.safe_load(patch_path.read_text())
    target = repo_root / patch["base"]["file"]
    current = sha256_path(target)
    if current != patch["base"]["sha256"]:
        raise PatchConflict(f"{patch['id']}: base hash {patch['base']['sha256'][:10]} != current {current[:10]} for {target.name}")
    text = target.read_text()
    for ch in patch["changes"]:
        pat = re.compile(rf'^(  {re.escape(ch["part"])}:.*?){re.escape(ch["field"])}: ("?){re.escape(ch["from"])}\2', re.M)
        if not pat.search(text):
            raise PatchConflict(f"{patch['id']}: {ch['part']}.{ch['field']} is not currently {ch['from']!r}")
        text = pat.sub(lambda m: f'{m.group(1)}{ch["field"]}: {m.group(2)}{ch["to"]}{m.group(2)}', text, count=1)
        for key, new in ch.get("also", {}).items():
            pat2 = re.compile(rf'^(  {re.escape(ch["part"])}:.*?){key}: "[^"]*"', re.M)
            if not pat2.search(text):
                raise PatchConflict(f"{patch['id']}: {ch['part']}.{key} not found")
            text = pat2.sub(lambda m: f'{m.group(1)}{key}: "{new}"', text, count=1)
    if not dry_run:
        target.write_text(text)
    yaml.safe_load(text)    # the result must still parse
    return {"id": patch["id"], "old_sha256": current, "new_sha256": __import__("hashlib").sha256(text.encode()).hexdigest(), "invalidates": patch.get("invalidates", [])}
