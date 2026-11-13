"""Exact-binary version selection for reviewed native sends. No WeChat calls."""
import hashlib
import json
import os
import re
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRIVATE = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'wechat2codex/private'

class NativeProfileError(RuntimeError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.details = {'error_code': code, 'message_sent': False, **details}

def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()

def installed_version(dll_path):
    name = Path(dll_path).parent.name
    return name if re.fullmatch(r'\d+\.\d+\.\d+\.\d+', name) else 'unknown'

def profiles(kind, root=ROOT):
    if kind not in ('text', 'file'):
        raise ValueError('Expected text or file')
    root = Path(root)
    legacy = root if kind == 'text' else root / 'file-adapter'
    items = []
    if (legacy / 'profile.json').is_file():
        p = json.loads((legacy / 'profile.json').read_text(encoding='utf8'))
        items.append((p, legacy / ('wechat-send.dll' if kind == 'text' else 'wechat-file-send.dll')))
    for file in sorted((root / 'versions').glob('*/profile.json')):
        p = json.loads(file.read_text(encoding='utf8'))
        helper = p.get('helpers', {}).get(kind)
        if helper:
            if Path(helper).name != helper:
                raise NativeProfileError('invalid_helper_path', 'Native helper must be inside its version directory')
            items.append((p, file.parent / helper))
    return items

def select_profile(dll_path, kind, root=ROOT):
    digest = sha256_file(dll_path)
    available = profiles(kind, root)
    matches = [(p, h) for p, h in available if p.get('sha256') == digest]
    if not matches:
        raise NativeProfileError('unsupported_wechat_binary',
            'No native send profile matches this exact Weixin.dll; update the adapter without replacing the expected hash.',
            actual_version=installed_version(dll_path), actual_sha256=digest,
            supported_versions=sorted({p['version'] for p, _ in available}),
            action='New-version static relocation and a non-submitting object probe are required.')
    if len(matches) != 1:
        raise NativeProfileError('ambiguous_native_profile', 'Multiple native profiles match this binary; no calls made')
    profile, helper = matches[0]
    if not helper.is_file():
        raise NativeProfileError('native_helper_missing', 'The selected native helper is missing', version=profile['version'])
    actual_helper = sha256_file(helper)
    expected_helper = profile.get('helper_sha256', {}).get(kind)
    if expected_helper and actual_helper != expected_helper:
        raise NativeProfileError('native_helper_hash_mismatch', 'Native helper changed; rebuild and verify it before use', version=profile['version'])
    profile = dict(profile)
    profile['_helper_sha256'] = actual_helper
    return profile, helper

def runtime_directory(profile, kind):
    return PRIVATE / 'send/runtime' / profile['version'] / kind

def proof_matches(proof, pid, profile):
    return (proof.get('status') == 'object_layout_verified'
            and proof.get('pid') == pid
            and proof.get('sha256') == profile['sha256']
            and proof.get('native_sha256') == profile['_helper_sha256']
            and bool(profile.get('_process_created'))
            and proof.get('process_created') == profile['_process_created'])

def require_probe(proof, pid, profile):
    if not proof_matches(proof, pid, profile):
        raise NativeProfileError('native_probe_required',
            'Run a non-submitting probe for this exact process, Weixin.dll and native helper build first.',
            version=profile['version'])

def error_output(error):
    output = {'status': 'error', 'error': str(error)}
    if isinstance(error, NativeProfileError):
        output.update(error.details)
    return output


def probe_state(directory, pid, profile):
    try:
        proof = json.loads((Path(directory) / 'runtime-probe.json').read_text(encoding='utf8'))
    except (FileNotFoundError, ValueError, OSError):
        return 'probe_required'
    return 'object_layout_verified' if proof_matches(proof, pid, profile) else 'probe_required'


def process_created(pid, process_factory=None):
    if process_factory is None:
        import psutil
        process_factory = psutil.Process
    return str(process_factory(pid).create_time())

def require_sender_account(pid, expected_account, expected_created, process_factory=None):
    if process_factory is None:
        import psutil
        process_factory = psutil.Process
    process = process_factory(pid)
    if str(process.create_time()) != expected_created:
        raise NativeProfileError('wechat_process_changed', 'The WeChat process changed; re-inspect and prepare a new draft.')
    try:
        open_files = process.open_files()
    except Exception as error:
        import psutil
        if process_factory is not psutil.Process or not isinstance(error, psutil.AccessDenied):
            raise
        try:
            # psutil already obtained the native handle paths; its high-level
            # method can fail while stat-ing an unrelated protected deleted file.
            # Preserve the same native enumeration and inspect only db_storage
            # candidates rather than discarding the full handle list.
            import psutil._pswindows as pswindows
            import stat
            from types import SimpleNamespace
            candidates = []
            for raw in pswindows.cext.proc_open_files(pid):
                path = pswindows.convert_dos_path(raw)
                if re.search(r'[\\/]xwechat_files[\\/]([^\\/]+)[\\/]db_storage[\\/]', path, re.I):
                    if not stat.S_ISREG(os.stat(path).st_mode):
                        raise RuntimeError('Opened database path is not a regular file')
                    candidates.append(SimpleNamespace(path=path))
            open_files = candidates
        except Exception as inspect_error:
            raise NativeProfileError('wechat_account_unresolved', 'Unable to inspect the process database files; nothing submitted.') from inspect_error
    if str(process.create_time()) != expected_created:
        raise NativeProfileError('wechat_process_changed', 'The WeChat process changed during account inspection; nothing submitted.')
    accounts = set()
    for file in open_files:
        match = re.search(r'[\\/]xwechat_files[\\/]([^\\/]+)[\\/]db_storage[\\/]', file.path, re.I)
        if match:
            accounts.add(match.group(1))
    if len(accounts) != 1:
        raise NativeProfileError('wechat_account_unresolved',
            'Cannot identify one sending account from this process open database files; nothing submitted.')
    account = accounts.pop()
    if account != expected_account:
        raise NativeProfileError('wechat_account_changed',
            'The live process account differs from the reviewed sending account; prepare a new draft.')
    return account

def write_probe(directory, value):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / 'runtime-probe.json'
    temporary = target.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf8')
    os.replace(temporary, target)

def begin_probe(directory, pid, profile):
    write_probe(directory, {'status': 'probe_started', 'pid': pid,
        'process_created': profile['_process_created'], 'sha256': profile['sha256'],
        'native_sha256': profile['_helper_sha256'], 'message_sent': False})
