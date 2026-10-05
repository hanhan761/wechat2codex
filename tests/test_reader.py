import importlib.util, pathlib, tempfile, unittest
p = pathlib.Path(__file__).resolve().parents[1] / "scripts/wechat.py"
spec = importlib.util.spec_from_file_location("reader",p)
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)
class ReaderTests(unittest.TestCase):
    def test_quote_and_forward(self):
        x = '<msg><appmsg><type>57</type><title>reply</title><refermsg><displayname>A</displayname><content>hello</content></refermsg><recorditem>&lt;recordinfo&gt;&lt;dataitem&gt;&lt;sourcename&gt;B&lt;/sourcename&gt;&lt;datadesc&gt;task&lt;/datadesc&gt;&lt;/dataitem&gt;&lt;/recordinfo&gt;</recorditem></appmsg></msg>'
        out = reader.xml_summary(x)
        self.assertEqual(out["quote"]["content"],"hello")
        self.assertEqual(out["forwarded"][0]["datadesc"],"task")
    def test_link_is_not_file(self):
        out = reader.xml_summary("<msg><appmsg><type>5</type><title>link</title><appattach/></appmsg></msg>")
        self.assertNotIn("attachment", out)
    def test_structured_image_does_not_export_keys(self):
        out = reader.xml_summary('<msg><img aeskey="secret"/></msg>')
        self.assertNotIn("secret", str(out))
    def test_identity_includes_chat_and_sequence(self):
        row={"sort_seq":1,"local_id":2,"create_time":3,"type":"text"}
        self.assertNotEqual(reader.identity("a","g1",row),reader.identity("a","g2",row))
        row2=dict(row,sort_seq=4)
        self.assertNotEqual(reader.identity("a","g1",row),reader.identity("a","g1",row2))
    def test_xml_entity_rejected(self):
        self.assertTrue(reader.xml_summary('<!DOCTYPE a [<!ENTITY x SYSTEM "file:///secret">]><a>&x;</a>')["xml_rejected"])
    def test_file_md5_resolves_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            for sub, data in (("a", b"one"), ("b", b"two")):
                d = root / "msg" / "file" / "2026-10" / sub
                d.mkdir(parents=True)
                (d / "report.txt").write_bytes(data)
            db = type("DB", (), {"account_dir": tmp})()
            row = {"create_time": 1791194400, "content": "<msg><appmsg><type>6</type><title>report.txt</title><appattach><totallen>3</totallen><md5>f97c5d29941bfb1b2fdab0874906ab82</md5></appattach></appmsg></msg>"}
            result = reader.file_copy(db, "group", row, root / "out")
            self.assertEqual(result["status"], "available")
            self.assertEqual(pathlib.Path(result["path"]).read_bytes(), b"one")
    def test_file_duplicate_not_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp)
            for sub,data in (("a",b"one"),("b",b"two")):
                d=root/"msg"/"file"/"2026-10"/sub
                d.mkdir(parents=True)
                (d/"report.txt").write_bytes(data)
            db=type("DB",(),{"account_dir":tmp})()
            row={"create_time":1791194400,"content":"<msg><appmsg><type>6</type><title>report.txt</title><appattach><totallen>3</totallen></appattach></appmsg></msg>"}
            self.assertEqual(reader.file_copy(db,"group",row,root/"out")["status"],"ambiguous")
if __name__ == "__main__":
    unittest.main()
