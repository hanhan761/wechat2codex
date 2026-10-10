"""Read-only local WeChat bridge. All runtime data lives outside this repository."""
import argparse, contextlib, datetime as dt, hashlib, importlib, importlib.metadata
import io, json, logging, os, pathlib, re, shutil, sqlite3, subprocess, sys, types
import xml.etree.ElementTree as ET
from forwarded import parse_record, recover_forwarded

VERSION = "1.2.4.4"
BASE = pathlib.Path(os.environ.get("LOCALAPPDATA", str(pathlib.Path.home() / "AppData" / "Local"))) / "wechat2codex"
PRIVATE = BASE / "private"

def protect():
    PRIVATE.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        who = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW, check=True)
        match = re.search(r"S-1-[0-9-]+", who.stdout)
        if not match:
            raise RuntimeError("Cannot identify current Windows user")
        user = "*" + match.group(0)
        p = subprocess.run(["icacls", str(PRIVATE), "/inheritance:r", "/grant:r",
                            user + ":(OI)(CI)F", "SYSTEM:(OI)(CI)F"],
                           capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if p.returncode:
            raise RuntimeError("Cannot restrict private runtime directory ACL")

def backend():
    if importlib.metadata.version("wechatauto-replica") != VERSION:
        raise RuntimeError("Run scripts/setup.py to install the pinned backend")
    pkg = types.ModuleType("wechatauto")
    pkg.__path__ = [str(importlib.metadata.distribution("wechatauto-replica").locate_file("wechatauto"))]
    sys.modules["wechatauto"] = pkg
    quiet = types.ModuleType("wechatauto.logger")
    quiet.wxlog = logging.getLogger("wechat2codex.upstream")
    quiet.wxlog.addHandler(logging.NullHandler())
    quiet.wxlog.propagate = False
    sys.modules["wechatauto.logger"] = quiet
    return importlib.import_module("wechatauto.db"), importlib.import_module("wechatauto.media")

@contextlib.contextmanager
def hidden_children():
    original = subprocess.Popen
    class HiddenPopen(original):
        def __init__(self, *args, **kwargs):
            if os.name == "nt":
                kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
            super().__init__(*args, **kwargs)
    subprocess.Popen = HiddenPopen
    try:
        yield
    finally:
        subprocess.Popen = original

def open_db(args):
    protect()
    mod, media = backend()
    class PrivateDB(mod.WeChatDB):
        def _stable_key_dirs(self):
            return [str(PRIVATE / "keys")]
        def _try_other_accounts(self):
            return False if args.account else super()._try_other_accounts()
        def _select_account_by_keys(self, candidates):
            return False if args.account else super()._select_account_by_keys(candidates)
    # Upstream may emit diagnostics; do not leak key material or raw DB paths into JSON.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        db = PrivateDB(db_dir=args.db_root, account=args.account,
                       workdir=str(PRIVATE / "database"), keys_file=str(PRIVATE / "keys.json"))
    workdir = PRIVATE / "database" / db.account
    workdir.mkdir(parents=True, exist_ok=True)
    db.workdir = str(workdir)
    db.keys_file = str(workdir / "keys.json")
    db._save_keys()
    return db, media

def xml_summary(content):
    if not isinstance(content, str):
        return {"text": ""}
    if len(content) > 2_000_000:
        return {"text": "" if "<appmsg" in content else content[:10000], "truncated": True}
    pos = content.find("<")
    if pos < 0:
        return {"text": content}
    raw = content[pos:]
    if "<!DOCTYPE" in raw.upper() or "<!ENTITY" in raw.upper():
        return {"text": "", "xml_rejected": True}
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        if "<appmsg" in raw or raw.lstrip().startswith(("<msg", "<?xml")):
            return {"text": "", "xml_status": "unparsed"}
        return {"text": content}
    app = root if root.tag == "appmsg" else root.find(".//appmsg")
    if app is None:
        return {"text": "", "structured_type": root.tag}
    out = {"app_type": app.findtext("type"), "title": app.findtext("title") or "",
           "description": app.findtext("des") or ""}
    for tag in ("url",):
        value = app.findtext(tag)
        if value:
            out[tag] = value
    quote = app.find("refermsg")
    if quote is not None:
        out["quote"] = {k: quote.findtext(k) or "" for k in ("displayname", "content", "createtime")}
        out["quote"]["content"] = out["quote"]["content"][:10000]
    record = app.findtext("recorditem")
    if record:
        parsed = parse_record(record)
        out["forwarded_status"] = parsed["status"]
        if parsed["status"] in ("parsed", "truncated"):
            out["forwarded"] = parsed["items"]
    attach = app.find("appattach")
    if attach is not None and out["app_type"] == "6":
        out["attachment"] = {"name": out["title"], "size": attach.findtext("totallen"),
                              "md5": attach.findtext("md5") or app.findtext("md5")}
    return out

def identity(account, chat, row):
    values = [account, chat, str(row.get("sort_seq")), str(row.get("local_id")),
              str(row.get("create_time")), str(row.get("type", row.get("local_type")))]
    return hashlib.sha256("\0".join(values).encode()).hexdigest()

@contextlib.contextmanager
def ledger():
    con = sqlite3.connect(PRIVATE / "receipts.sqlite3")
    try:
        con.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, chat TEXT, payload TEXT, processed TEXT, result TEXT)")
        with con:
            yield con
    finally:
        con.close()

def chat_info(db, session):
    user = session["username"]
    return {"chat": user, "name": db.get_nickname(user) or user,
            "group": user.endswith("@chatroom"), "unread": session.get("unread", 0),
            "last_time": session.get("last_time")}

def resolve(db, value):
    if value.endswith("@chatroom") or value.startswith("wxid_"):
        return value
    hits = db.search_contact(value)
    exact = [x for x in hits if value in (x.get("username"), x.get("nick_name"), x.get("remark"))]
    chosen = exact or hits
    if len(chosen) != 1:
        raise ValueError("Chat name is ambiguous or absent; use a chat ID from chats")
    return chosen[0]["username"]

def hash_file(path, algorithm="sha256"):
    digest = hashlib.new(algorithm)
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

def file_copy(db, chat, row, dest, summary=None):
    summary = summary or xml_summary(row.get("content") or "")
    meta = summary.get("attachment", {})
    name = meta.get("name") or ""
    if not name:
        return {"status": "local_missing", "reason": "No attachment filename"}
    name = re.split(r"[/\\]", name)[-1]
    root = pathlib.Path(db.account_dir) / "msg" / "file"
    month = dt.datetime.fromtimestamp(row["create_time"]).strftime("%Y-%m")
    expected_md5 = meta.get("md5")
    digest_known = bool(expected_md5 and re.fullmatch(r"[a-fA-F0-9]{32}", expected_md5))
    # WeChat appends (1), (2), ... when an already-named file is downloaded.
    # Accept those aliases only with a full message MD5, never by filename alone.
    original = pathlib.Path(name)
    duplicate_name = re.compile(re.escape(original.stem) + r"(?: ?\([1-9][0-9]*\))+" + re.escape(original.suffix) + r"$", re.IGNORECASE)
    candidates = [p for p in root.rglob("*") if p.is_file()
                  and (p.name == name or (digest_known and duplicate_name.fullmatch(p.name)))
                  and p.resolve().is_relative_to(root.resolve())] if root.exists() else []
    size = meta.get("size")
    if size and str(size).isdigit():
        candidates = [p for p in candidates if p.stat().st_size == int(size)]
    if expected_md5 and re.fullmatch(r"[a-fA-F0-9]{32}", expected_md5):
        candidates = [p for p in candidates if hash_file(p,"md5").lower() == expected_md5.lower()]
    else:
        candidates = [p for p in candidates if month in p.parts]
    if not candidates:
        return {"status": "local_missing"}
    hashes = {hash_file(p) for p in candidates}
    if len(hashes) != 1:
        return {"status": "ambiguous", "reason": "Multiple different local files match"}
    dest.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(". ")[:140] or "attachment"
    target = dest / safe
    shutil.copy2(candidates[0], target)
    return {"status": "available", "path": str(target), "sha256": next(iter(hashes)),
            "source_path": str(candidates[0]), "matched_by": "md5" if digest_known else "month_filename"}

def run(args):
    db, media = open_db(args)
    if args.command == "doctor":
        sessions = db.get_sessions(limit=100)
        return {"status": "ready" if not db.unkeyed else "partial",
                "backend": VERSION, "account": db.account, "database_count": len(db._db_files),
                "unreadable_database_count": len(db.unkeyed),
                "sessions_checked": len(sessions),
                "groups_checked": sum(s["username"].endswith("@chatroom") for s in sessions),
                "private_directory": str(PRIVATE)}
    if args.command == "chats":
        rows = [chat_info(db,s) for s in db.get_sessions(limit=args.limit)]
        if args.groups:
            rows = [s for s in rows if s["group"]]
        if args.unread:
            rows = [s for s in rows if s["unread"]]
        return {"chats": rows}
    if args.command == "read":
        chat = resolve(db,args.chat)
        rows = db.get_messages(chat,limit=args.limit,offset=args.offset)
        sender_index = db._sender_id_index()
        output = []
        with ledger() as con:
            for row in rows:
                if args.since and row["create_time"] < args.since:
                    continue
                normalized = xml_summary(row.get("content") or "")
                if args.contains and args.contains not in json.dumps(normalized,ensure_ascii=False):
                    continue
                mid = identity(db.account,chat,row)
                previous = con.execute("SELECT processed FROM seen WHERE id=?",(mid,)).fetchone()
                if args.pending and previous and previous[0]:
                    continue
                sender = sender_index.get(int(row.get("sender_id") or 0),row.get("sender_username") or "")
                item = {"id":mid,"account":db.account,"chat":chat,"group":chat.endswith("@chatroom"),
                        "local_id":row["local_id"],"sort_seq":row.get("sort_seq"),
                        "time":row["create_time"],"type":row.get("type"),
                        "sender":sender,"sender_name":db.get_nickname(sender) if sender else None,
                        "message":normalized,"processed":bool(previous and previous[0])}
                con.execute("INSERT INTO seen(id,chat,payload) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                            (mid,chat,json.dumps(item,ensure_ascii=False)))
                output.append(item)
        return {"chat":chat,"messages":output,"limit":args.limit,"offset":args.offset,
                "note":"Newest first; use offset for older pages. Reading does not mark processed."}
    if args.command == "media":
        with ledger() as con:
            observed = con.execute("SELECT chat,payload FROM seen WHERE id=?",(args.id,)).fetchone()
        if not observed:
            raise ValueError("Read the message first and pass its observed id")
        chat,payload = observed[0],json.loads(observed[1])
        if payload.get("account") != db.account:
            raise ValueError("Receipt belongs to another account")
        rows = db.get_message_rows_for_media(chat,payload["local_id"])
        # Upstream media lookup uses local_id; reject shard collisions instead of guessing.
        unique = {(r["sort_seq"],r["create_time"],r["local_type"]) for r in rows}
        if len(unique) != 1 or not rows or rows[0]["sort_seq"] != payload["sort_seq"]:
            return {"status":"ambiguous","reason":"local_id collision or changed message"}
        row = rows[0]
        dest = PRIVATE / "attachments" / args.id
        base_type = int(row["local_type"]) & 0xFFFFFFFF
        if base_type == 49:
            # Decode the exact collision-checked source row, including old receipts
            # written before forwarded attachment metadata was supported.
            content = row.get("content") or ""
            if isinstance(content, bytes):
                content = db._friendly_content(content, "文件/链接/卡片")
            compressed = row.get("compress_content")
            if "<" not in content and isinstance(compressed, bytes):
                content = db._friendly_content(compressed, "文件/链接/卡片")
            summary = xml_summary(content)
            if summary.get("app_type") == "19" or "forwarded_status" in summary:
                if summary.get("forwarded_status") not in ("parsed", "truncated"):
                    return {"status":"unparsed", "reason":"forwarded_record_could_not_be_parsed"}
                with hidden_children(), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    result = recover_forwarded(db, media, summary.get("forwarded", []),
                                               dest / "forwarded", row["create_time"], getattr(args, "item", None))
                result["parent_message_id"] = args.id
                result["record_status"] = summary["forwarded_status"]
                return result
            if getattr(args, "item", None):
                return {"status":"invalid_item", "reason":"item_selector_requires_forwarded_record"}
            return file_copy(db,chat,row,dest,summary)
        if getattr(args, "item", None):
            return {"status":"invalid_item", "reason":"item_selector_requires_forwarded_record"}
        dest.mkdir(parents=True,exist_ok=True)
        downloader = media.MediaDownloader(db,save_dir=str(dest))
        with hidden_children(), contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            if row["local_type"] == 3:
                path = downloader.download_image(chat,row["local_id"],tier="best")
            elif row["local_type"] == 34:
                path = downloader.download_voice(chat,row["local_id"])
            elif row["local_type"] == 43:
                path = downloader.download_video(chat,row["local_id"])
            else:
                return {"status":"unsupported","type":row["local_type"]}
        if not path:
            return {"status":"local_missing"}
        path = pathlib.Path(path).resolve()
        if not path.is_relative_to(dest.resolve()):
            raise RuntimeError("Media backend returned a path outside the private attachment directory")
        return {"status":"available","path":str(path),"sha256":hash_file(path)}
    raise ValueError("Unknown command")

def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account")
    parser.add_argument("--db-root")
    sub = parser.add_subparsers(dest="command",required=True)
    sub.add_parser("doctor")
    chats = sub.add_parser("chats")
    chats.add_argument("--limit",type=int,default=100)
    chats.add_argument("--groups",action="store_true")
    chats.add_argument("--unread",action="store_true")
    read = sub.add_parser("read")
    read.add_argument("--chat",required=True)
    read.add_argument("--limit",type=int,default=50)
    read.add_argument("--offset",type=int,default=0)
    read.add_argument("--since",type=int)
    read.add_argument("--contains")
    read.add_argument("--pending",action="store_true")
    media = sub.add_parser("media")
    media.add_argument("--id",required=True)
    media.add_argument("--item",help="Forwarded item ID from read, e.g. 2 or 1.3")
    ack = sub.add_parser("ack")
    ack.add_argument("--id",required=True,action="append")
    ack.add_argument("--result",required=True)
    args = parser.parse_args()
    try:
        if hasattr(args,"limit") and not 1 <= args.limit <= 500:
            raise ValueError("limit must be 1..500")
        if getattr(args,"offset",0) < 0:
            raise ValueError("offset must be nonnegative")
        if args.command == "ack":
            protect()
            with ledger() as con:
                for mid in args.id:
                    if not con.execute("SELECT 1 FROM seen WHERE id=?",(mid,)).fetchone():
                        raise ValueError("Unknown message receipt")
                for mid in args.id:
                    con.execute("UPDATE seen SET processed=?,result=? WHERE id=?",
                                (dt.datetime.now(dt.timezone.utc).isoformat(),args.result,mid))
            result = {"processed":args.id}
        else:
            result = run(args)
        print(json.dumps(result,ensure_ascii=False,default=str))
    except Exception as exc:
        # Avoid printing backend exception text, which may contain credential material.
        print(json.dumps({"status":"error","error_type":type(exc).__name__,
                          "action":"Check login, backend installation, selected account and local database path."}))
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
