import io
import json
import os
import unittest
import uuid
from email.message import Message
from unittest.mock import Mock, patch

from scripts.chat_mail import AuthenticationError, create_task, make_handler, MAX_REQUEST_BYTES
from scripts.github_api import GitHubAPIError
from scripts.task_queue import TaskError, TaskStore, Tasks, new_task
from scripts import task_runner
from test_task_queue import TaskAPI, NOW


class ChatMailTests(unittest.TestCase):
    def setUp(self):
        self.api = TaskAPI()
        self.data = {'subject': '紫瞳夜话', 'body': '外部生成的聊天正文', 'request_id': str(uuid.uuid4())}
        env = patch.dict(os.environ, {'CHAT_MAIL_TOKEN': 'test-only-token',
                                     'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1'}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def create(self, data=None):
        return create_task(self.api, data or self.data, 'Bearer test-only-token', now=NOW)

    def test_normal_creation_is_private_custom_one_shot(self):
        result = self.create()
        task = TaskStore(self.api).get(result['task_id'])
        self.assertTrue(result['created'])
        self.assertFalse(result['repeat'])
        self.assertEqual(task['content']['kind'], 'custom')
        self.assertIsNone(task['repeat'])
        self.assertEqual(task['status'], 'queued')
        self.assertNotIn(self.data['body'], json.dumps(self.api.queue, ensure_ascii=False))
        self.assertEqual(len(self.api.assets), 1)

    def test_same_request_id_never_uploads_or_overwrites_again(self):
        self.create()
        other = dict(self.data, body='不同正文')
        result = self.create(other)
        self.assertFalse(result['created'])
        self.assertEqual(result['duplicate'], 'request_id')
        self.assertEqual(len(self.api.assets), 1)
        self.assertEqual(len(self.api.queue['tasks']), 1)

    def test_same_body_different_subject_never_sends_twice(self):
        first = self.create()
        later = dict(self.data, subject='换标题', request_id=str(uuid.uuid4()))
        result = self.create(later)
        self.assertEqual(result['duplicate'], 'body_sha256')
        self.assertEqual(result['duplicate_of'], first['task_id'])
        self.assertEqual(result['status'], 'cancelled')
        sender = Mock(return_value={'accepted_at': NOW.isoformat()})
        task_runner.run(self.api, now=lambda: NOW, sender=sender)
        task_runner.run(self.api, now=lambda: NOW, sender=sender)
        sender.assert_called_once()
        # The duplicate request's tombstone also reserves its ID forever.
        self.assertFalse(self.create(dict(later, body='新正文'))['created'])

    def test_missing_wrong_or_unconfigured_token_has_no_side_effects(self):
        for token in (None, '', 'Bearer wrong', 'test-only-token'):
            with self.subTest(token=token), self.assertRaises(AuthenticationError):
                create_task(self.api, self.data, token)
        with patch.dict(os.environ, {'CHAT_MAIL_TOKEN': ''}):
            with self.assertRaises(AuthenticationError):
                self.create()
        self.assertEqual(self.api.calls, [])
        self.assertIsNone(self.api.queue)

    def test_invalid_payload_and_repeat_fields_fail_before_writes(self):
        for data in (dict(self.data, repeat=True), dict(self.data, body=''),
                     dict(self.data, subject='a\nb'), dict(self.data, request_id='invalid'),
                     dict(self.data, body='\ud800'), dict(self.data, subject=5)):
            with self.subTest(data=data), self.assertRaises(TaskError):
                self.create(data)
        self.assertEqual(self.api.calls, [])

    def test_existing_custom_snapshot_hash_is_reused(self):
        import hashlib
        raw = json.dumps({'subject': self.data['subject'], 'body': self.data['body']}, ensure_ascii=False).encode()
        old = new_task(str(uuid.uuid4()), {'kind': 'custom', 'payload': {
            'asset_id': 20, 'release_id': 10, 'sha256': hashlib.sha256(raw).hexdigest()}}, NOW.isoformat(), now=NOW)
        Tasks(TaskStore(self.api)).create(old)
        self.assertFalse(self.create()['created'])
        self.assertFalse(self.api.assets)

    def test_upload_failure_preserves_tombstone_and_never_reuploads(self):
        with patch.object(self.api, 'upload_release_asset', side_effect=GitHubAPIError('unknown')) as upload:
            with self.assertRaises(GitHubAPIError):
                self.create()
            self.assertEqual(self.create()['status'], 'failed')
            upload.assert_called_once()
        self.assertFalse(self.create(dict(self.data, request_id=str(uuid.uuid4())))['created'])

    def test_cas_conflict_only_retries_reservation_not_upload(self):
        self.api.conflict = True
        self.assertTrue(self.create()['created'])
        self.assertEqual(len(self.api.assets), 1)

    def test_uncertain_reservation_never_uploads(self):
        self.api.uncertain_write = True
        with self.assertRaises(GitHubAPIError):
            self.create()
        self.assertFalse(self.api.assets)

    def test_concurrent_same_body_is_reserved_before_upload(self):
        original = self.api.upload_release_asset
        def upload(*args):
            duplicate = self.create(dict(self.data, request_id=str(uuid.uuid4())))
            self.assertFalse(duplicate['created'])
            return original(*args)
        with patch.object(self.api, 'upload_release_asset', side_effect=upload):
            self.assertTrue(self.create()['created'])
        self.assertEqual(len(self.api.assets), 1)

    def request(self, token='Bearer test-only-token', data=None, length=None):
        handler = make_handler(self.api).__new__(make_handler(self.api))
        handler.path = '/chat-mail/tasks'
        handler.headers = Message()
        if token is not None:
            handler.headers['Authorization'] = token
        raw = json.dumps(data or self.data).encode()
        handler.headers['Content-Type'] = 'application/json'
        handler.headers['Content-Length'] = str(len(raw) if length is None else length)
        handler.rfile = io.BytesIO(raw)
        handler.reply = Mock()
        handler.do_POST()
        return handler

    def test_http_create_and_idempotent_status_codes(self):
        self.assertEqual(self.request().reply.call_args.args[0], 201)
        self.assertEqual(self.request().reply.call_args.args[0], 200)

    def test_http_missing_token_is_401_before_reading_body(self):
        handler = self.request(token=None)
        self.assertEqual(handler.reply.call_args.args, (401, {'error': 'unauthorized'}))
        self.assertEqual(handler.rfile.tell(), 0)
        self.assertFalse(self.api.calls)

    def test_http_oversized_body_is_rejected_without_writes(self):
        self.assertEqual(self.request(length=MAX_REQUEST_BYTES + 1).reply.call_args.args[0], 400)
        self.assertFalse(self.api.calls)

    def test_http_uncertain_write_returns_safe_503(self):
        self.api.uncertain_write = True
        handler = self.request()
        self.assertEqual(handler.reply.call_args.args[0], 503)
        self.assertNotIn(self.data['body'], json.dumps(handler.reply.call_args.args))
