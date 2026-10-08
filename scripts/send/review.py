"""Private drafts and single-use approvals. No WeChat API is called here.

The approve command records a real user's post-preview reply. The skill must
verify that reply in the conversation; this CLI cannot authenticate its author.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

PRIVATE = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'wechat2codex/private'
REVIEW_ROOT = PRIVATE / 'send/reviews'


def protect_private():
    PRIVATE.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        who = subprocess.run(['whoami', '/user', '/fo', 'csv', '/nh'],
                             capture_output=True, text=True, check=True,
                             creationflags=subprocess.CREATE_NO_WINDOW)
        sid = re.search(r'S-1-[0-9-]+', who.stdout)
        if not sid:
            raise RuntimeError('Cannot identify current Windows user')
        result = subprocess.run(['icacls', str(PRIVATE), '/inheritance:r', '/grant:r',
                                 '*' + sid.group(0) + ':(OI)(CI)F', 'SYSTEM:(OI)(CI)F'],
                                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError('Cannot restrict private runtime directory ACL')


def fingerprint(payload):
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf8')
    return hashlib.sha256(raw).hexdigest()


def draft_path(draft_id):
    if not isinstance(draft_id, str) or not re.fullmatch(r'[0-9a-f]{12}', draft_id):
        raise RuntimeError('Specify the exact draft ID shown to the user')
    return REVIEW_ROOT / (draft_id + '.json')


def required_reply(draft_id):
    return '批准发送 ' + draft_id


def validate_target(to):
    if not to or '\0' in to or any(ord(c) < 33 for c in to) or len(to.encode('utf8')) > 255:
        raise RuntimeError('Use an exact WeChat ID of at most 255 UTF-8 bytes')


def text_bytes(text):
    raw = text.encode('utf8')
    if '\0' in text or not 0 < len(raw) < 3072:
        raise RuntimeError('UTF-8 text must contain 1 to 3071 bytes without NUL')
    return raw


def file_details(source):
    source = Path(source).resolve(strict=True)
    if not source.is_file():
        raise RuntimeError('Expected one regular file')
    if len(str(source).encode('utf-16-le')) // 2 >= 1024:
        raise RuntimeError('UTF-16 path limit 1023 code units')
    size = source.stat().st_size
    if not 0 < size <= 100 * 1024 * 1024 or len(source.name.encode('utf8')) >= 3072:
        raise RuntimeError('File must be 1 byte to 100 MiB with a supported filename')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    return {'file_path': str(source), 'file_name': source.name, 'file_size': size, 'file_sha256': digest}


def create_draft(kind, to, recipient, account, pid, *, text=None, file=None):
    validate_target(to)
    if not recipient or not account or not isinstance(pid, int) or pid <= 0:
        raise RuntimeError('Identify recipient, sending account and exact WeChat PID before drafting')
    payload = {'kind': kind, 'to': to, 'recipient': recipient, 'account': account, 'pid': pid}
    if kind == 'text':
        raw = text_bytes(text)
        payload.update(text=text, text_sha256=hashlib.sha256(raw).hexdigest())
    elif kind == 'file':
        payload.update(file_details(file))
    else:
        raise RuntimeError('Unsupported draft kind')
    protect_private()
    REVIEW_ROOT.mkdir(parents=True, exist_ok=True)
    draft_id = uuid.uuid4().hex[:12]
    if kind == 'file':
        folder = REVIEW_ROOT / draft_id
        folder.mkdir()
        preview = folder / payload['file_name']
        shutil.copyfile(payload['file_path'], preview)
        staged = file_details(preview)
        if staged['file_sha256'] != payload['file_sha256'] or staged['file_size'] != payload['file_size']:
            raise RuntimeError('File changed while drafting; nothing submitted')
        payload['review_file'] = str(preview)
    draft = {'draft_id': draft_id, 'status': 'pending_review', 'created_at': time.time(),
             'payload': payload, 'fingerprint': fingerprint(payload),
             'required_reply': required_reply(draft_id)}
    with draft_path(draft_id).open('x', encoding='utf8') as output:
        json.dump(draft, output, ensure_ascii=False, indent=2)
    return draft


def load_draft(draft_id):
    try:
        draft = json.loads(draft_path(draft_id).read_text(encoding='utf8'))
    except FileNotFoundError:
        raise RuntimeError('Draft not found; prepare and show a new draft first') from None
    if draft.get('draft_id') != draft_id or fingerprint(draft['payload']) != draft.get('fingerprint'):
        raise RuntimeError('Draft changed; prepare a new draft and obtain new human approval')
    if draft_path(draft_id).with_suffix('.used').exists():
        raise RuntimeError('Approval already consumed; do not retry a submission')
    if draft['payload']['kind'] == 'file':
        preview = file_details(draft['payload']['review_file'])
        if any(preview[key] != draft['payload'][key] for key in ('file_name', 'file_size', 'file_sha256')):
            raise RuntimeError('Reviewed file changed; prepare a new draft and obtain new human approval')
    return draft


def approve(draft_id, response):
    # Call only after this exact reply arrives from the human after full preview.
    draft = load_draft(draft_id)
    if response != required_reply(draft_id):
        raise RuntimeError('Approval must be the exact human reply: ' + required_reply(draft_id))
    if draft['status'] != 'pending_review':
        raise RuntimeError('This draft is not awaiting approval')
    draft['status'] = 'approved'
    draft['approval'] = {'response': response, 'approved_at': time.time(),
                         'fingerprint': draft['fingerprint']}
    location = draft_path(draft_id)
    temporary = location.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding='utf8')
    os.replace(temporary, location)
    return {'draft_id': draft_id, 'status': 'approved', 'message_sent': False}


def validate_review(draft_id, kind, to, *, pid=None, raw=None, file=None):
    draft = load_draft(draft_id)
    approval = draft.get('approval', {})
    if (draft.get('status') != 'approved'
            or approval.get('response') != required_reply(draft_id)
            or approval.get('fingerprint') != draft['fingerprint']):
        raise RuntimeError('Human approval required after showing the complete draft')
    payload = draft['payload']
    if payload['kind'] != kind or payload['to'] != to or (pid is not None and payload['pid'] != pid):
        raise RuntimeError('Recipient, account process or message kind differs from approved draft')
    if kind == 'text':
        if raw != payload['text'].encode('utf8') or hashlib.sha256(raw).hexdigest() != payload['text_sha256']:
            raise RuntimeError('Text differs from approved draft; obtain new human approval')
    elif kind == 'file':
        actual = file_details(file)
        if any(actual[key] != payload[key] for key in actual):
            raise RuntimeError('File differs from approved draft; obtain new human approval')
    else:
        raise RuntimeError('Unsupported message kind')
    return payload


def claim_review(draft_id, kind, to, **kwargs):
    payload = validate_review(draft_id, kind, to, **kwargs)
    # Exclusive creation prevents concurrent sends from sharing an approval.
    try:
        with draft_path(draft_id).with_suffix('.used').open('x', encoding='utf8') as output:
            json.dump({'draft_id': draft_id, 'claimed_at': time.time(),
                       'status': 'submission_started'}, output)
    except FileExistsError:
        raise RuntimeError('Approval already consumed; do not retry a submission') from None
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    for action in ('draft-text', 'draft-file'):
        command = commands.add_parser(action)
        for field in ('to', 'recipient', 'account'):
            command.add_argument('--' + field, required=True)
        command.add_argument('--pid', type=int, required=True)
        command.add_argument('--text-file' if action == 'draft-text' else '--file', type=Path, required=True)
    command = commands.add_parser('show')
    command.add_argument('--draft-id', required=True)
    command = commands.add_parser('approve')
    command.add_argument('--draft-id', required=True)
    command.add_argument('--response', required=True)
    args = parser.parse_args()
    if args.action == 'draft-text':
        out = create_draft('text', args.to, args.recipient, args.account, args.pid,
                           text=args.text_file.read_bytes().decode('utf8'))
    elif args.action == 'draft-file':
        out = create_draft('file', args.to, args.recipient, args.account, args.pid, file=args.file)
    elif args.action == 'show':
        out = load_draft(args.draft_id)
    else:
        out = approve(args.draft_id, args.response)
    print(json.dumps(out, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status': 'error', 'error': str(error)}, ensure_ascii=False))
        sys.exit(1)
