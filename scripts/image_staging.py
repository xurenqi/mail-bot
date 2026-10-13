"""Offline-only Base64 staging validation. No SMTP or GitHub write operations."""
import base64
import binascii
import hashlib
import json
import re
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024
PART_BYTES = 96 * 1024
UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
SHA = re.compile(r'^[0-9a-f]{64}$')
B64 = re.compile(r'^[A-Za-z0-9+/]*={0,2}$')

class StagingError(ValueError):
    pass

def validate_manifest(m):
    if not isinstance(m, dict) or set(m) != {'version','request_id','image_bytes','image_sha256','part_count','part_sha256'}:
        raise StagingError('invalid manifest')
    if type(m['version']) is not int or m['version'] != 1 or not isinstance(m['request_id'],str) or not UUID.fullmatch(m['request_id']):
        raise StagingError('invalid version or request id')
    size, count = m['image_bytes'], m['part_count']
    if type(size) is not int or not 0 < size <= MAX_BYTES or type(count) is not int or count != (size + PART_BYTES - 1)//PART_BYTES:
        raise StagingError('invalid image size or part count')
    if not isinstance(m['image_sha256'],str) or not SHA.fullmatch(m['image_sha256']):
        raise StagingError('invalid image hash')
    hashes = m['part_sha256']
    if not isinstance(hashes,list) or len(hashes) != count or any(not isinstance(x,str) or not SHA.fullmatch(x) for x in hashes):
        raise StagingError('invalid chunk hashes')
    return m

def encode_parts(data, rid):
    """Build text-only chunks for create_file; no GitHub or SMTP calls."""
    from .comic_mail import image_type
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_BYTES:
        raise StagingError('invalid image size')
    if not isinstance(rid, str) or not UUID.fullmatch(rid):
        raise StagingError('invalid request id')
    image_type(data)
    chunks = [data[i:i + PART_BYTES] for i in range(0, len(data), PART_BYTES)]
    manifest = {'version': 1, 'request_id': rid, 'image_bytes': len(data),
                'image_sha256': hashlib.sha256(data).hexdigest(),
                'part_count': len(chunks),
                'part_sha256': [hashlib.sha256(chunk).hexdigest() for chunk in chunks]}
    return manifest, [base64.b64encode(chunk).decode('ascii') for chunk in chunks]


def decode_parts(manifest, parts):
    m = validate_manifest(manifest)
    if len(parts) != m['part_count']:
        raise StagingError('missing or extra parts')
    output = bytearray()
    for i, text in enumerate(parts):
        if not isinstance(text,str) or len(text) > ((PART_BYTES+2)//3)*4 or not B64.fullmatch(text):
            raise StagingError('invalid encoded part')
        try:
            data = base64.b64decode(text, validate=True)
        except (ValueError,binascii.Error):
            raise StagingError('invalid Base64') from None
        if len(data) != min(PART_BYTES, m['image_bytes']-i*PART_BYTES) or hashlib.sha256(data).hexdigest() != m['part_sha256'][i]:
            raise StagingError('chunk mismatch')
        output.extend(data)
    if len(output) != m['image_bytes'] or hashlib.sha256(output).hexdigest() != m['image_sha256']:
        raise StagingError('image mismatch')
    from .comic_mail import image_type
    image_type(bytes(output))
    return bytes(output)

def read_staging(root, rid):
    if not isinstance(rid,str) or not UUID.fullmatch(rid):
        raise StagingError('invalid request id')
    folder = Path(root)/rid
    if folder.is_symlink() or not folder.is_dir():
        raise StagingError('invalid staging directory')
    manifest_path = folder/'manifest.json'
    if manifest_path.is_symlink() or not manifest_path.is_file() or manifest_path.stat().st_size > 8192:
        raise StagingError('invalid manifest file')
    m = validate_manifest(json.loads(manifest_path.read_text(encoding='utf-8')))
    if m['request_id'] != rid:
        raise StagingError('request mismatch')
    parts = []
    for i in range(m['part_count']):
        path = folder/'parts'/f'{i:04d}.txt'
        if path.is_symlink() or not path.is_file() or path.stat().st_size > ((PART_BYTES+2)//3)*4:
            raise StagingError('invalid part file')
        parts.append(path.read_text(encoding='ascii'))
    return decode_parts(m, parts)
