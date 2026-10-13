"""Offline tests: never trigger workflow dispatch or SMTP."""
import base64
import hashlib
import struct
import unittest
import uuid
import zlib

from scripts.image_staging import decode_parts, encode_parts, StagingError, PART_BYTES

def png():
    def chunk(name, data):
        return struct.pack('>I',len(data)) + name + data + struct.pack('>I',zlib.crc32(name+data))
    return (b'\x89PNG\r\n\x1a\n' +
            chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0)) +
            chunk(b'IDAT',zlib.compress(b'\x00\xff\x00\x00')) +
            chunk(b'IEND',b''))

def fixture():
    data = png()
    sha = hashlib.sha256(data).hexdigest()
    return data, {'version':1,'request_id':str(uuid.uuid4()),'image_bytes':len(data),
                  'image_sha256':sha,'part_count':1,'part_sha256':[sha]}

class TestStaging(unittest.TestCase):
    def test_encoder_roundtrip(self):
        data, manifest = fixture()
        generated, parts = encode_parts(data, manifest['request_id'])
        self.assertEqual(decode_parts(generated, parts), data)

    def test_encoder_rejects_invalid_data(self):
        _, manifest = fixture()
        with self.assertRaises(ValueError):
            encode_parts(b'not a picture', manifest['request_id'])

    def test_multi_chunk_png(self):
        # Ancillary PNG chunk makes a valid file larger than one staging part.
        data = png()
        import zlib
        payload = b'x' * (PART_BYTES + 1024)
        segment = (len(payload).to_bytes(4,'big') + b'tEXt' + payload +
                   (zlib.crc32(b'tEXt' + payload) & 0xffffffff).to_bytes(4,'big'))
        data = data[:-12] + segment + data[-12:]
        generated, parts = encode_parts(data, str(uuid.uuid4()))
        self.assertEqual(len(parts), 2)
        self.assertEqual(decode_parts(generated, parts), data)
        with self.assertRaises(StagingError):
            decode_parts(generated, list(reversed(parts)))

    def test_valid_png(self):
        data, manifest = fixture()
        self.assertEqual(decode_parts(manifest,[base64.b64encode(data).decode()]),data)

    def test_corrupt_chunk(self):
        data, manifest = fixture()
        damaged = bytearray(data)
        damaged[-5] ^= 1
        with self.assertRaises(StagingError):
            decode_parts(manifest,[base64.b64encode(damaged).decode()])

    def test_missing_chunk(self):
        _, manifest = fixture()
        with self.assertRaises(StagingError):
            decode_parts(manifest,[])

    def test_extra_manifest_field(self):
        data, manifest = fixture()
        manifest['action'] = 'send'
        with self.assertRaises(StagingError):
            decode_parts(manifest,[base64.b64encode(data).decode()])

    def test_invalid_png_structure(self):
        data, manifest = fixture()
        data = data[:-4] + b'ABCD'
        sha = hashlib.sha256(data).hexdigest()
        manifest['image_sha256'] = sha
        manifest['part_sha256'] = [sha]
        with self.assertRaises(ValueError):
            decode_parts(manifest,[base64.b64encode(data).decode()])

if __name__ == '__main__':
    unittest.main()
