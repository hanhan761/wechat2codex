"""Regression checks for reading forwarded records and local nested attachments."""
import argparse
import hashlib
import html
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
from PIL import Image
import wechat as w
RUNTIME_ROOT = w.PRIVATE / "tests"
RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)


def record_xml(items):
    return '<recordinfo><datalist>' + ''.join(items) + '</datalist></recordinfo>'


def card(record):
    return '<msg><appmsg><type>19</type><title>Chat record</title><recorditem>' + html.escape(record) + '</recorditem></appmsg></msg>'


def item(kind, body, dataid='a' * 32):
    return '<dataitem datatype="' + str(kind) + '" dataid="' + dataid + '"><sourcename>Alice</sourcename><sourcetime>2026-10-10 13:37:41</sourcetime>' + body + '</dataitem>'


class ForwardedTests(unittest.TestCase):
    def test_read_preserves_media_identity_and_no_credentials(self):
        xml = card(record_xml([item(1, '<datadesc>Join these slides</datadesc>'), item(2, '<datadesc>[图片]</datadesc><fullmd5>' + 'b'*32 + '</fullmd5><datasize>1234</datasize><cdndatakey>SECRET-KEY</cdndatakey><cdnthumburl>SECRET-URL</cdnthumburl>')]))
        result = w.xml_summary(xml)
        image = result['forwarded'][1]
        self.assertEqual(image['item_id'], '2')
        self.assertEqual(image['media']['kind'], 'image')
        self.assertEqual(image['media']['md5'], 'b'*32)
        self.assertNotIn('SECRET', json.dumps(result))

    def test_literal_ampersand_and_xml_entities_are_preserved(self):
        result = w.xml_summary(card(record_xml([item(1, '<datadesc>R&amp;D &lt; 5</datadesc>')])))
        self.assertEqual(result['forwarded'][0]['datadesc'], 'R&D < 5')

    def test_nested_records_remain_hierarchical(self):
        nested = record_xml([item(2, '<datadesc>[图片]</datadesc><fullmd5>'+'b'*32+'</fullmd5>')])
        result = w.xml_summary(card(record_xml([item(17, '<recorditem>'+html.escape(nested)+'</recorditem>')])))
        self.assertEqual(len(result['forwarded']), 1)
        self.assertEqual(result['forwarded'][0]['forwarded'][0]['item_id'], '1.1')

    def test_unsafe_nested_xml_is_reported(self):
        evil = '<!DOCTYPE x [<!ENTITY e "unsafe">]><recordinfo><datalist/></recordinfo>'
        result = w.xml_summary(card(evil))
        self.assertIn(result.get('forwarded_status'), ('rejected', 'unparsed'))
        self.assertNotIn('forwarded', result)

    def _run_media(self, root, xml, selector=None, older_payload=False, duplicate=False):
        row = {'local_id':29,'sort_seq':1791611342000,'create_time':1791611342,'local_type':49,'content':xml.encode(),'compress_content':None}
        class DB:
            account = 'wxid_fixture'
            account_dir = str(root / 'account')
            def get_message_rows_for_media(self, chat, local_id):
                return [row, dict(row, sort_seq=1)] if duplicate else [row]
            @staticmethod
            def _friendly_content(content, mtype):
                return content.decode('utf8') if isinstance(content, bytes) else content
        payload = {'account':DB.account,'local_id':29,'sort_seq':row['sort_seq'],'message':{'app_type':'19'} if older_payload else w.xml_summary(xml)}
        old_private = w.PRIVATE
        w.PRIVATE = root / 'private'
        w.PRIVATE.mkdir(exist_ok=True)
        try:
            with w.ledger() as con:
                con.execute('INSERT INTO seen(id,chat,payload) VALUES(?,?,?)', ('message', 'group@chatroom', json.dumps(payload)))
            with patch.object(w,'open_db',return_value=(DB(),types.SimpleNamespace())):
                return w.run(argparse.Namespace(command='media',id='message',item=selector))
        finally:
            w.PRIVATE = old_private

    def test_actual_media_route_recovers_image_from_stale_ledger(self):
        with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as td:
            root = Path(td)
            dest = root/'account'/'cache'/'record'
            dest.mkdir(parents=True)
            stream = io.BytesIO(); Image.new('RGB',(30,20),'navy').save(stream,'PNG')
            blob=stream.getvalue(); digest=hashlib.md5(blob).hexdigest()
            (dest/'opaque-cache-name.png').write_bytes(blob)
            xml=card(record_xml([item(2,'<datadesc>[图片]</datadesc><fullmd5>'+digest+'</fullmd5><datasize>'+str(len(blob))+'</datasize>')]))
            result=self._run_media(root,xml,older_payload=True)
            self.assertEqual(result['status'],'forwarded')
            self.assertEqual(result['summary']['available'],1)
            nested=result['items'][0]
            self.assertEqual(Path(nested['path']).read_bytes(),blob)
            self.assertTrue(Path(nested['path']).resolve().is_relative_to((root/'private').resolve()))

    def test_missing_images_are_individually_reported(self):
        with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as td:
            xml=card(record_xml([item(2,'<datadesc>[图片]</datadesc><fullmd5>'+'b'*32+'</fullmd5>'),item(2,'<datadesc>[图片]</datadesc><fullmd5>'+'c'*32+'</fullmd5>')]))
            result=self._run_media(Path(td),xml)
            self.assertEqual(result['status'],'forwarded')
            self.assertEqual(result['summary']['local_missing'],2)
            self.assertEqual([i['item_id'] for i in result['items']],['1','2'])

    def test_selector_is_stable_and_returns_one_item(self):
        with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as td:
            xml=card(record_xml([item(1,'<datadesc>Intro</datadesc>'),item(2,'<datadesc>[图片]</datadesc>')]))
            result=self._run_media(Path(td),xml,selector='2')
            self.assertEqual(result['item_id'],'2')
            self.assertEqual(result['status'],'local_missing')

    def test_shard_collision_does_not_recover_arbitrary_attachment(self):
        with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as td:
            result=self._run_media(Path(td),card(record_xml([item(2,'<datadesc>[图片]</datadesc>')])),duplicate=True)
            self.assertEqual(result['status'],'ambiguous')


    def test_file_metadata_cannot_escape_private_directory(self):
        with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as td:
            root=Path(td); cached=root/'account'/'cache';cached.mkdir(parents=True)
            blob=b'example document';digest=hashlib.md5(blob).hexdigest()
            (cached/'opaque.bin').write_bytes(blob)
            xml=card(record_xml([item(8,'<datatitle>../../evil.txt</datatitle><fullmd5>'+digest+'</fullmd5><datasize>'+str(len(blob))+'</datasize>')]))
            result=self._run_media(root,xml,selector='1')
            self.assertEqual(result['status'],'available')
            self.assertEqual(Path(result['path']).name,'evil.txt')
            self.assertTrue(Path(result['path']).is_relative_to(root/'private'))

    def test_duplicate_named_cache_entries_are_not_arbitrarily_selected(self):
        from forwarded import recover_forwarded
        with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as td:
            root=Path(td)
            for index,color in enumerate(['navy','red']):
                folder=root/'account'/'cache'/str(index);folder.mkdir(parents=True)
                stream=io.BytesIO();Image.new('RGB',(30,20),color).save(stream,'PNG')
                (folder/('a'*32+'_h.dat')).write_bytes(stream.getvalue())
            class Downloader:
                def __init__(self,*a,**kw):pass
                def decrypt_image(self,path):return Path(path).read_bytes()
            entries=w.xml_summary(card(record_xml([item(2,'<datadesc>[图片]</datadesc>')])))['forwarded']
            result=recover_forwarded(types.SimpleNamespace(account_dir=str(root/'account')),types.SimpleNamespace(MediaDownloader=Downloader),entries,root/'private',1791611342)
            self.assertEqual(result['items'][0]['status'],'ambiguous')

    def test_cached_thumbnail_is_never_called_original(self):
        from forwarded import recover_forwarded
        with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as td:
            root=Path(td);folder=root/'account'/'cache';folder.mkdir(parents=True)
            stream=io.BytesIO();Image.new('RGB',(30,20),'navy').save(stream,'PNG')
            (folder/('a'*32+'_t.dat')).write_bytes(stream.getvalue())
            class Downloader:
                def __init__(self,*a,**kw):pass
                def decrypt_image(self,path):return Path(path).read_bytes()
            entries=w.xml_summary(card(record_xml([item(2,'<datadesc>[图片]</datadesc>')])))['forwarded']
            result=recover_forwarded(types.SimpleNamespace(account_dir=str(root/'account')),types.SimpleNamespace(MediaDownloader=Downloader),entries,root/'private',1791611342)
            self.assertEqual(result['items'][0]['status'],'available')
            self.assertEqual(result['items'][0]['quality'],'thumbnail')

    def test_xml_embedded_in_nested_description_does_not_leak_keys(self):
        nested=record_xml([item(2,'<datadesc>[图片]</datadesc><cdndatakey>SECRET-KEY</cdndatakey>')])
        parsed=w.xml_summary(card(record_xml([item(17,'<datadesc>'+html.escape(nested)+'</datadesc>')])))
        self.assertEqual(parsed['forwarded'][0]['forwarded'][0]['item_id'],'1.1')
        self.assertNotIn('SECRET',json.dumps(parsed))

    def test_global_item_limit_is_explicit(self):
        parsed=w.xml_summary(card(record_xml([item(1,'<datadesc>Text</datadesc>') for _ in range(501)])))
        self.assertEqual(len(parsed['forwarded']),500)
        self.assertEqual(parsed['forwarded_status'],'truncated')

    def test_regular_file_card_still_uses_normal_file_path(self):
        with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as td:
            root=Path(td);folder=root/'account'/'msg'/'file'/'2026-10';folder.mkdir(parents=True)
            blob=b'ordinary file';digest=hashlib.md5(blob).hexdigest()
            (folder/'report.txt').write_bytes(blob)
            xml='<msg><appmsg><type>6</type><title>report.txt</title><appattach><totallen>'+str(len(blob))+'</totallen><md5>'+digest+'</md5></appattach></appmsg></msg>'
            result=self._run_media(root,xml)
            self.assertEqual(result['status'],'available')
            self.assertEqual(Path(result['path']).read_bytes(),blob)


    def test_unparsed_nested_records_are_not_silently_empty(self):
        with tempfile.TemporaryDirectory(dir=RUNTIME_ROOT) as td:
            xml=card(record_xml([item(17,'<datadesc>Another record</datadesc>')]))
            result=self._run_media(Path(td),xml)
            self.assertEqual(result['availability'],'unknown')
            self.assertEqual(result['unresolved_records'],[{'item_id':'1','status':'unparsed'}])

    def test_malformed_structured_card_does_not_expose_transport_secrets(self):
        parsed=w.xml_summary('<msg><appmsg><cdndatakey>SECRET-KEY</cdndatakey>')
        self.assertEqual(parsed['xml_status'],'unparsed')
        self.assertNotIn('SECRET',json.dumps(parsed))

    def test_plain_text_with_angle_brackets_stays_plain_text(self):
        self.assertEqual(w.xml_summary('R&D < 5')['text'],'R&D < 5')

if __name__ == '__main__':
    unittest.main()
