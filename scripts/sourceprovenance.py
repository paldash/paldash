"""Canonical table selection and reproducible provenance for offline bundles."""
import hashlib
import json
from pathlib import Path
import re


def canonical_path(pak, name):
    suffix = '/' + name.removesuffix('.uasset') + '.uasset'
    paths = sorted(p for p in pak.files if p.endswith(suffix))
    preferred = [p for p in paths if '/L10N/' not in p]
    if len(preferred) != 1:
        raise ValueError(f'{name}: expected one nonlocalized source, found {len(preferred)}')
    return preferred[0]


def digest(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def opacity(value):
    counts = {}
    def walk(node):
        if isinstance(node, str):
            match = re.fullmatch(r'<([^<>]+?) (\d+)B(?:,.*?)?>', node)
            if match:
                counts[match[1]] = counts.get(match[1], 0) + 1
            elif node.startswith('<') and node.endswith('>') and any(word in node for word in ('undecoded', 'not tagged', 'overran')):
                counts['undecoded array'] = counts.get('undecoded array', 0) + 1
        elif isinstance(node, dict):
            for key, item in node.items():
                if key == '_opaque':
                    label = str(item).rsplit(' ', 1)[0]
                    counts[label] = counts.get(label, 0) + 1
                else:
                    walk(item)
        elif isinstance(node, list):
            for item in node: walk(item)
    walk(value)
    return counts


def archive(pak):
    cached = getattr(pak, '_source_provenance', None)
    if cached is None:
        with open(pak.path, 'rb') as handle:
            checksum = hashlib.file_digest(handle, 'sha256').hexdigest()
        cached = {'archive': Path(pak.path).name, 'bytes': Path(pak.path).stat().st_size, 'sha256': checksum}
        pak._source_provenance = cached
    return cached
