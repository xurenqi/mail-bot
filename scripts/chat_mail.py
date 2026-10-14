"""Authenticated creation adapter; delivery belongs exclusively to task_runner."""
import argparse
import copy
import hashlib
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

from .github_api import GitHubAPI
from .task_queue import TaskError, TaskStore, new_task, task_id
from .task_runner import private_payload
from .time_utils import format_utc, utc_now
from .send_mail import MAX_CUSTOM_BODY_CHARACTERS, MAX_CUSTOM_SUBJECT_CHARACTERS

MAX_REQUEST_BYTES = 512 * 1024


class AuthenticationError(TaskError):
    pass


def authenticate(authorization):
    expected = os.environ.get('CHAT_MAIL_TOKEN', '')
    if (not expected or not isinstance(authorization, str)
            or not hmac.compare_digest(authorization.encode('utf-8'),
                                       ('Bearer ' + expected).encode('utf-8'))):
        raise AuthenticationError('聊天邮件认证失败。')


def create_task(api, data, authorization, *, now=None):
    authenticate(authorization)  # Before validation or any GitHub access.
    if not isinstance(data, dict) or set(data) != {'subject', 'body', 'request_id'}:
        raise TaskError('必须且只能提供 subject、body、request_id。')
    subject, body = data['subject'], data['body']
    rid = task_id(data['request_id'])
    if (not isinstance(subject, str) or not subject.strip()
            or len(subject) > MAX_CUSTOM_SUBJECT_CHARACTERS
            or any(not c.isprintable() for c in subject)):
        raise TaskError('主题必须是非空单行文本，最多 200 个字符。')
    if (not isinstance(body, str) or not body.strip() or '\0' in body
            or len(body) > MAX_CUSTOM_BODY_CHARACTERS):
        raise TaskError('正文必须非空、无空字符，最多 100000 个字符。')
    try:
        raw = json.dumps({'subject': subject, 'body': body}, ensure_ascii=False).encode('utf-8')
        body_hash = hashlib.sha256(body.encode('utf-8')).hexdigest()
    except UnicodeError:
        raise TaskError('内容必须是合法 UTF-8 文本。') from None
    if len(raw) > MAX_REQUEST_BYTES:
        raise TaskError('邮件内容超过私有文件大小限制。')
    fingerprint = hashlib.sha256(raw).hexdigest()
    now = now or utc_now()
    store = TaskStore(api)
    task = new_task(rid, {'kind': 'custom'}, format_utc(now), repeat=None,
                    now=now, status='staging')
    task['chat_mail'] = {'body_sha256': body_hash, 'payload_sha256': fingerprint}

    def reserve(records):
        candidate = copy.deepcopy(task)
        if rid in records:
            return records[rid], False, 'request_id'
        for previous in records.values():
            if previous['content']['kind'] != 'custom':
                continue
            chat = previous.get('chat_mail', {})
            if (chat.get('body_sha256') == body_hash
                    or chat.get('payload_sha256') == fingerprint
                    or previous['content'].get('payload', {}).get('sha256') == fingerprint):
                # Keep an alias tombstone so this request ID can never be reused.
                candidate.update(status='cancelled', reason='duplicate_chat_content',
                            finished_at=format_utc(now), duplicate_of=previous.get('duplicate_of', previous['id']))
                records[rid] = candidate
                return candidate, False, 'body_sha256'
        records[rid] = candidate
        return candidate, True, None

    saved, created, duplicate = store.update(reserve)
    if created:
        prepared = {}
        try:
            prepared['payload'] = private_payload(api, rid, subject, body)

            def ready(records):
                current = records[rid]
                if current['status'] != 'staging':
                    raise TaskError('任务已结束，不能重新排队。')
                current['content'].update(prepared)
                current['status'] = 'queued'
                return current
            saved = store.update(ready)
        except Exception:
            def failed(records):
                current = records[rid]
                current['content'].update(prepared)
                if current['status'] == 'staging':
                    current.update(status='failed', reason='content_preparation_failed',
                                   finished_at=format_utc(now))
                return current
            try:
                store.update(failed)
            except Exception:
                pass  # Uncertain writes keep the reservation; never upload again.
            raise
    return {'task_id': saved['id'], 'status': saved['status'], 'created': created,
            'duplicate': duplicate, 'duplicate_of': saved.get('duplicate_of'), 'repeat': False}


def make_handler(api):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def log_message(self, *args):
            pass  # Never log client-controlled paths, credentials or content.

        def reply(self, status, result):
            raw = json.dumps(result, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            try:
                authorization = self.headers.get('Authorization')
                authenticate(authorization)
                if self.path != '/chat-mail/tasks':
                    self.reply(404, {'error': 'not_found'})
                    return
                length = int(self.headers.get('Content-Length', '0'))
                if (self.headers.get('Transfer-Encoding') or not 0 < length <= MAX_REQUEST_BYTES
                        or self.headers.get_content_type() != 'application/json'):
                    raise TaskError('请求必须是有长度限制的 JSON。')
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise TaskError('请求内容不完整。')
                result = create_task(api, json.loads(raw), authorization)
                self.reply(201 if result['created'] else 200, result)
            except AuthenticationError:
                self.reply(401, {'error': 'unauthorized'})
            except (TaskError, ValueError, UnicodeError):
                self.reply(400, {'error': 'invalid_request'})
            except Exception:
                self.reply(503, {'error': 'creation_unconfirmed',
                                 'message': '请查询原 request_id；禁止更换编号或自动重试。'})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, help='明确指定 owner/name')
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    if not os.environ.get('CHAT_MAIL_TOKEN'):
        parser.error('必须配置 CHAT_MAIL_TOKEN')
    api = GitHubAPI(args.repo, os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN'))
    with HTTPServer(('127.0.0.1', args.port), make_handler(api)) as server:
        server.serve_forever()


if __name__ == '__main__':
    main()
