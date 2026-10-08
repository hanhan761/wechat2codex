"""Offline approval tests. All native adapter operations are mocked."""
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'wechat-send/scripts'
sys.path.insert(0, str(SCRIPTS))
import review


class ReviewFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        for name, value in [('PRIVATE', self.base / 'private'),
                            ('REVIEW_ROOT', self.base / 'private/send/reviews')]:
            handle = patch.object(review, name, value)
            handle.start()
            self.addCleanup(handle.stop)
        handle = patch.object(review, 'protect_private')
        handle.start()
        self.addCleanup(handle.stop)

    def text_draft(self, text='审核内容\r\n  第二行  \r\n'):
        return review.create_draft('text', 'test_id', '测试收件人', '测试账号', 321, text=text)

    def file_draft(self):
        source = self.base / '审核文件.txt'
        source.write_bytes(b'original attachment')
        return source, review.create_draft('file', 'test_id', '测试收件人', '测试账号', 321, file=source)

    def approve(self, draft):
        review.approve(draft['draft_id'], draft['required_reply'])


class ReviewTests(ReviewFixture):
    def test_initial_request_and_unspecific_approval_cannot_send(self):
        draft = self.text_draft()
        with self.assertRaisesRegex(RuntimeError, 'Human approval required'):
            review.validate_review(draft['draft_id'], 'text', 'test_id', raw=draft['payload']['text'].encode())
        for response in ['发微信', '同意', '批准发送', '批准发送 other', draft['required_reply'] + ' ']:
            with self.assertRaises(RuntimeError):
                review.approve(draft['draft_id'], response)
        self.assertEqual(review.load_draft(draft['draft_id'])['status'], 'pending_review')

    def test_full_text_recipient_kind_and_process_are_bound(self):
        draft = self.text_draft()
        self.approve(draft)
        raw = draft['payload']['text'].encode()
        self.assertEqual(review.validate_review(draft['draft_id'], 'text', 'test_id', raw=raw)['pid'], 321)
        for changed in [raw.replace(b'\r\n', b'\n'), raw.rstrip(), raw + b'extra']:
            with self.assertRaisesRegex(RuntimeError, 'Text differs'):
                review.validate_review(draft['draft_id'], 'text', 'test_id', raw=changed)
        for kind, target, pid in [('text', 'other_id', 321), ('file', 'test_id', 321), ('text', 'test_id', 322)]:
            with self.assertRaises(RuntimeError):
                review.validate_review(draft['draft_id'], kind, target, pid=pid, raw=raw)

    def test_draft_tampering_requires_new_review(self):
        draft = self.text_draft()
        self.approve(draft)
        location = review.draft_path(draft['draft_id'])
        record = json.loads(location.read_text(encoding='utf8'))
        record['payload']['text'] = 'different'
        location.write_text(json.dumps(record), encoding='utf8')
        with self.assertRaisesRegex(RuntimeError, 'Draft changed'):
            review.load_draft(draft['draft_id'])

    def test_attachment_change_and_rename_are_rejected(self):
        source, draft = self.file_draft()
        self.approve(draft)
        review.validate_review(draft['draft_id'], 'file', 'test_id', file=source)
        renamed = self.base / 'renamed.txt'
        renamed.write_bytes(source.read_bytes())
        with self.assertRaisesRegex(RuntimeError, 'File differs'):
            review.validate_review(draft['draft_id'], 'file', 'test_id', file=renamed)
        source.write_bytes(b'changed! attachment')
        with self.assertRaisesRegex(RuntimeError, 'File differs'):
            review.validate_review(draft['draft_id'], 'file', 'test_id', file=source)

    def test_attachment_preview_cannot_change_after_review(self):
        source, draft = self.file_draft()
        preview = Path(draft['payload']['review_file'])
        self.assertEqual(preview.read_bytes(), source.read_bytes())
        preview.write_bytes(b'changed preview')
        with self.assertRaisesRegex(RuntimeError, 'Reviewed file changed'):
            self.approve(draft)

    def test_approval_is_single_use_even_with_concurrent_claims(self):
        draft = self.text_draft()
        self.approve(draft)
        def claim():
            try:
                review.claim_review(draft['draft_id'], 'text', 'test_id', raw=draft['payload']['text'].encode())
                return True
            except RuntimeError:
                return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(lambda _: claim(), range(4))), 1)
        with self.assertRaisesRegex(RuntimeError, 'already consumed'):
            self.approve(draft)

    def test_draft_cli_preserves_utf8_bytes_and_does_not_approve(self):
        source = self.base / 'text.txt'
        raw = '中文\r\n  保留空白  \r\n'.encode()
        source.write_bytes(raw)
        args = ['review.py', 'draft-text', '--to', 'test_id', '--recipient', '测试人',
                '--account', '测试账号', '--pid', '321', '--text-file', str(source)]
        output = io.StringIO()
        with patch.object(sys, 'argv', args), contextlib.redirect_stdout(output):
            review.main()
        draft = json.loads(output.getvalue())
        self.assertEqual(draft['payload']['text'].encode(), raw)
        self.assertEqual(draft['status'], 'pending_review')
        self.assertNotIn('approval', draft)

    def test_missing_or_traversal_draft_id_is_rejected(self):
        for draft_id in [None, '', '../test', '0' * 12]:
            with self.assertRaises(RuntimeError):
                review.validate_review(draft_id, 'text', 'test_id', raw=b'test')


@unittest.skipUnless(os.name == 'nt', 'Native loader imports require Windows; no live calls are used')
class AdapterGateTests(ReviewFixture):
    @classmethod
    def setUpClass(cls):
        cls.adapters = []
        for index, source in enumerate([SCRIPTS / 'adapter.py', SCRIPTS / 'file-adapter/adapter.py']):
            spec = importlib.util.spec_from_file_location('offline_adapter_' + str(index), source)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            cls.adapters.append(module)

    @contextlib.contextmanager
    def mock_native(self, module, args, fail=False):
        native_root = self.base / 'native'
        native_root.mkdir(exist_ok=True)
        (native_root / 'runtime-probe.json').write_text(json.dumps({
            'pid': 321, 'sha256': module.PROFILE['sha256'], 'status': 'object_layout_verified'}))
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(sys, 'argv', ['adapter.py'] + args))
            stack.enter_context(patch.object(module, 'ROOT', native_root))
            selected = stack.enter_context(patch.object(module, 'select', return_value=(321, [], {})))
            for function, value in [('openprocess', 1), ('verify', None), ('close', None)]:
                stack.enter_context(patch.object(module, function, return_value=value))
            result = SimpleNamespace(status=3, exception=0, type=49, reserved=6)
            executed = stack.enter_context(patch.object(module, 'execute', return_value=result,
                side_effect=RuntimeError('mock outcome unknown') if fail else None))
            stack.enter_context(patch.dict(os.environ, {'LOCALAPPDATA': str(self.base)}))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            yield selected, executed

    def test_both_unapproved_sends_stop_before_process_access(self):
        text_draft = self.text_draft()
        source, file_draft = self.file_draft()
        cases = [(self.adapters[0], ['send', '--to', 'test_id', '--text', text_draft['payload']['text']], text_draft),
                 (self.adapters[1], ['send-file', '--to', 'test_id', '--file', str(source)], file_draft)]
        for module, args, draft in cases:
            for suffix in [[], ['--draft-id', draft['draft_id']]]:
                with self.mock_native(module, args + suffix + ['--experimental']) as (selected, executed):
                    with self.assertRaises(RuntimeError):
                        module.main()
                    selected.assert_not_called()
                    executed.assert_not_called()

    def test_both_approved_sends_consume_once_and_use_exact_content(self):
        draft = self.text_draft()
        source, attachment = self.file_draft()
        cases = [(self.adapters[0], ['send', '--to', 'test_id', '--text', draft['payload']['text']], draft),
                 (self.adapters[1], ['send-file', '--to', 'test_id', '--file', str(source)], attachment)]
        for module, args, record in cases:
            self.approve(record)
            command = args + ['--draft-id', record['draft_id'], '--experimental']
            with self.mock_native(module, command) as (selected, executed):
                module.main()
                selected.assert_called_once_with(321)
                executed.assert_called_once()
                request = executed.call_args.args[-1]
                self.assertEqual(request.receiver, b'test_id')
                if record['payload']['kind'] == 'text':
                    self.assertEqual(request.text, record['payload']['text'].encode())
                else:
                    staged = Path(request.path)
                    self.assertEqual(staged.name, source.name)
                    self.assertEqual(staged.read_bytes(), source.read_bytes())
            with self.mock_native(module, command) as (selected, executed):
                with self.assertRaisesRegex(RuntimeError, 'already consumed'):
                    module.main()
                selected.assert_not_called()
                executed.assert_not_called()

    def test_unknown_native_outcome_does_not_allow_retry(self):
        draft = self.text_draft()
        source, attachment = self.file_draft()
        cases = [(self.adapters[0], ['send', '--to', 'test_id', '--text', draft['payload']['text']], draft),
                 (self.adapters[1], ['send-file', '--to', 'test_id', '--file', str(source)], attachment)]
        for module, args, record in cases:
            self.approve(record)
            command = args + ['--draft-id', record['draft_id'], '--experimental']
            with self.mock_native(module, command, fail=True) as (_, executed):
                with self.assertRaisesRegex(RuntimeError, 'outcome unknown'):
                    module.main()
                executed.assert_called_once()
            with self.mock_native(module, command) as (selected, executed):
                with self.assertRaisesRegex(RuntimeError, 'already consumed'):
                    module.main()
                selected.assert_not_called()
                executed.assert_not_called()


if __name__ == '__main__':
    unittest.main()
