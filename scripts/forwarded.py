"""Parse forwarded records and recover verified local copies of their attachments.

Only allowlisted metadata leaves the parser. CDN addresses and decryption keys
are intentionally excluded; this module never requests remote media.
"""
import collections
import datetime as dt
import hashlib
import html
import io
import os
from pathlib import Path
import re
import stat
import struct
import xml.etree.ElementTree as ET

MAX_RECORD_SIZE = 2_000_000
MAX_DEPTH = 5
MAX_ITEMS = 500
KINDS = {'1':'text', '2':'image', '3':'voice', '4':'video', '5':'link', '8':'file', '17':'forwarded'}


def _record_root(value):
    if isinstance(value, ET.Element):
        return value
    text = value or ''
    if len(text) > MAX_RECORD_SIZE:
        raise ValueError('record_too_large')
    # ElementTree already unescapes the containing recorditem once. Try it first
    # so R&amp;D stays valid; only unwrap another escaped layer if parsing fails.
    for _ in range(3):
        if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
            raise ValueError('unsafe_record_xml')
        try:
            return ET.fromstring(text)
        except ET.ParseError:
            unwrapped = html.unescape(text)
            if unwrapped == text:
                raise
            text = unwrapped
    raise ET.ParseError('unparsed_record')


def _meta(item, kind):
    aliases = {'fullmd5':'md5', 'thumbfullmd5':'thumb_md5', 'datasize':'size',
               'thumbsize':'thumb_size', 'datafmt':'extension'}
    out = {'kind':kind}
    dataid = item.get('dataid', '')
    if re.fullmatch(r'[a-fA-F0-9]{16,64}', dataid):
        out['data_id'] = dataid.lower()
    for xml_name, key in aliases.items():
        value = item.findtext(xml_name)
        if value:
            out[key] = value[:200]
    # Some records include original message media attributes in dataitemsource.
    source = item.find('dataitemsource')
    if source is not None:
        source_text = source.text or ''
        if '<' in source_text:
            try:
                source = _record_root(source_text)
            except (ET.ParseError, ValueError):
                pass
        for el in source.iter():
            for key in ('filemd5','md5','filesize','midfilesize','thumbfiletypesize'):
                value = el.get(key) or (el.text if el.tag == key else None)
                mapped = {'filemd5':'file_md5', 'md5':'file_md5', 'filesize':'file_size',
                          'midfilesize':'mid_size', 'thumbfiletypesize':'thumb_file_size'}[key]
                if value and mapped not in out:
                    out[mapped] = value[:200]
    if kind == 'file':
        name = item.findtext('datatitle') or item.findtext('datadesc') or ''
        out['name'] = name[:500]
    return out


def parse_record(value, prefix='', depth=0, budget=None):
    budget = budget if budget is not None else [MAX_ITEMS]
    if depth >= MAX_DEPTH:
        return {'items':[], 'status':'truncated', 'reason':'depth_limit'}
    try:
        root = _record_root(value)
    except ValueError:
        return {'items':[], 'status':'rejected'}
    except ET.ParseError:
        return {'items':[], 'status':'unparsed'}
    listing = root if root.tag == 'datalist' else root.find('datalist')
    if listing is None:
        return {'items':[], 'status':'unparsed'}
    result = []
    status = 'parsed'
    for index, node in enumerate(listing.findall('dataitem'), 1):
        if budget[0] <= 0:
            status = 'truncated'
            break
        budget[0] -= 1
        item_id = f'{prefix}.{index}' if prefix else str(index)
        entry = {key:node.findtext(key) or '' for key in
                 ('sourcename','sourcetime','datadesc','datatitle')}
        dtype = node.get('datatype') or node.findtext('datatype') or ''
        kind = KINDS.get(dtype, 'unknown')
        entry.update(item_id=item_id, datatype=dtype, type=kind)
        if kind in ('image','file','voice','video'):
            entry['media'] = _meta(node, kind)
        nested = node.find('recordinfo')
        nested_value = nested if nested is not None else node.findtext('recorditem')
        if nested_value is None and kind == 'forwarded' and '<' in entry['datadesc']:
            nested_value = entry['datadesc']
        if nested_value is not None:
            if nested_value == entry['datadesc']:
                entry['datadesc'] = ''
            children = parse_record(nested_value,item_id,depth+1,budget)
            entry['forwarded_status'] = children['status']
            if children['status'] in ('parsed','truncated'):
                entry['forwarded'] = children['items']
            if children['status'] == 'truncated':
                status = 'truncated'
        if kind == 'forwarded' and nested_value is None:
            entry['forwarded_status'] = 'unparsed'
        result.append(entry)
    return {'items':result,'status':status}


def media_items(entries):
    for item in entries:
        if item.get('media'):
            yield item
        yield from media_items(item.get('forwarded',[]))


def record_issues(entries):
    for item in entries:
        status = item.get('forwarded_status')
        if status and status != 'parsed':
            yield {'item_id':item['item_id'],'status':status}
        yield from record_issues(item.get('forwarded',[]))


def _digest(value):
    value = str(value or '').lower()
    # Some WeChat records omit a leading zero from a hexadecimal MD5 string.
    return value.zfill(32) if re.fullmatch(r'[a-f0-9]{16,32}', value) else None


def _local_files(account_dir):
    account = Path(account_dir).resolve()
    for relative in ('msg/attach','msg/file','cache','resource','temp'):
        root = account / relative
        if not root.is_dir() or not root.resolve().is_relative_to(account):
            continue
        pending = [root]
        while pending:
            directory = pending.pop()
            try:
                with os.scandir(directory) as scan:
                    for entry in scan:
                        try:
                            details = entry.stat(follow_symlinks=False)
                            # Reject Windows junctions as well as symlinks; resolve
                            # only matching candidates, not every cached file.
                            if stat.S_ISLNK(details.st_mode) or getattr(details,'st_file_attributes',0) & 0x400:
                                continue
                            if stat.S_ISDIR(details.st_mode):
                                pending.append(Path(entry.path))
                            elif stat.S_ISREG(details.st_mode):
                                yield Path(entry.path), details.st_size
                        except OSError:
                            continue
            except OSError:
                continue


def _quality(path, meta, matched_hash=None):
    name = path.stem.lower()
    if matched_hash and matched_hash == _digest(meta.get('thumb_md5')):
        return 'thumbnail'
    if name.endswith(('_t','_thumb')):
        return 'thumbnail'
    if matched_hash in {_digest(meta.get('md5')), _digest(meta.get('file_md5'))} - {None}:
        return 'original'
    return 'preview' if path.suffix.lower() == '.dat' else 'unknown'


def _named_identity(path, meta):
    stem = path.stem.lower()
    stem = re.sub(r'_(?:h|t|thumb)$', '', stem)
    identities = {str(meta.get('data_id','')).lower()}
    identities.update(str(meta.get(k,'')).lower() for k in ('md5','file_md5','thumb_md5'))
    identities.update(_digest(meta.get(k)) for k in ('md5','file_md5','thumb_md5'))
    return stem in identities - {'',None}


def _safe_name(value, default):
    name = re.split(r'[/\\]', value or default)[-1]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).rstrip('. ')[:140]
    return name


def _encoded_image_candidate(path, size, sizes):
    """Match the plaintext length in a V1/V2 header, never the numeric Rec name.

    Forwarded pictures are also stored as Rec/<record>/Img/1 and 1_t, with
    no .dat extension. V2 adds a 15-byte header and 1..16 AES padding bytes.
    Length is only a candidate filter; decrypted content MD5 must still match.
    """
    if not sizes or size < 22 or not any(16 <= size - value <= 31 for value in sizes):
        return False
    try:
        with path.open('rb') as stream:
            head = stream.read(15)
        if head[:6] == b'\x07\x08V2\x08\x07':
            aes_size, xor_size = struct.unpack_from('<LL', head, 6)
            aes_block = aes_size + 16 - aes_size % 16
            if 15 + aes_block + xor_size > size:
                return False
            plain_size = size - 15 - (aes_block - aes_size)
            return plain_size in sizes
        if head[:6] == b'\x07\x08V1\x08\x07':
            return size - 22 in sizes
    except (OSError, struct.error):
        pass
    return False


def _decrypt_image_bytes(downloader, media_module, path):
    # Upstream trims small container footers. Forwarded fullmd5/datasize may
    # describe the bytes INCLUDING that footer, so retain it until hash checking.
    # The bridge is single-threaded; always restore the backend helper.
    strip = getattr(media_module,'strip_container_footer',None)
    if strip is None:
        return downloader.decrypt_image(str(path))
    try:
        media_module.strip_container_footer = lambda data: (data,0)
        return downloader.decrypt_image(str(path))
    finally:
        media_module.strip_container_footer = strip


def _recover_one(db, media_module, entry, files, dest, create_time):
    meta = entry['media']
    hashes = {_digest(meta.get(k)) for k in ('md5','file_md5','thumb_md5')} - {None}
    sizes = {int(meta[k]) for k in ('size','file_size','mid_size','thumb_size','thumb_file_size')
             if str(meta.get(k,'')).isdigit()}
    kind = meta['kind']
    candidates = []
    month = dt.datetime.fromtimestamp(create_time).strftime('%Y-%m') if create_time else None
    name = _safe_name(meta.get('name'),'')
    for path, size in files:
        named = _named_identity(path, meta)
        name_only = kind == 'file' and name and path.name == name and size in sizes and month in path.parts
        # Hash lookup recovers opaque record-cache filenames without assuming
        # that the original chat-local image ID is a forwarded image ID.
        by_hash = False
        matched_hash = None
        if hashes and path.suffix.lower() != '.dat' and (named or size in sizes):
            digest = hashlib.md5(path.read_bytes()).hexdigest()
            if digest in hashes:
                by_hash, matched_hash = True, digest
            elif named and kind != 'image':
                # A plaintext file with a known content digest must verify it.
                continue
        encrypted_by_size = kind == 'image' and bool(hashes) and _encoded_image_candidate(path,size,sizes)
        if named or by_hash or encrypted_by_size or (name_only and not hashes):
            candidates.append((path,_quality(path,meta,matched_hash),encrypted_by_size))
    if not candidates:
        return {'status':'local_missing','reason':'no_verified_local_copy'}
    ranks = {'original':0,'preview':1,'unknown':2,'thumbnail':3}
    candidates.sort(key=lambda value:ranks[value[1]])
    decoded = []
    errors = []
    downloader = None
    for path, quality, encrypted_by_size in candidates:
        try:
            if not path.resolve().is_relative_to(Path(db.account_dir).resolve()):
                continue
            if path.suffix.lower() == '.dat' or encrypted_by_size:
                if kind != 'image':
                    continue
                if downloader is None:
                    downloader = media_module.MediaDownloader(db,save_dir=str(dest))
                blob = _decrypt_image_bytes(downloader,media_module,path)
                if blob[:4] == b'wxgf':
                    blob = downloader._wxgf_to_jpg(blob)
                    if not blob:
                        raise ValueError('unsupported_local_image_codec')
            else:
                blob = path.read_bytes()
            if kind == 'image':
                blob_digest = hashlib.md5(blob).hexdigest()
                if hashes and blob_digest not in hashes:
                    continue
                quality = _quality(path, meta, blob_digest if blob_digest in hashes else None)
                from PIL import Image
                with Image.open(io.BytesIO(blob)) as image:
                    dimensions = list(image.size)
                    image.verify()
                    fmt = image.format
                extension = {'JPEG':'jpg','PNG':'png','GIF':'gif','WEBP':'webp','BMP':'bmp','TIFF':'tiff'}.get(fmt)
                if not extension:
                    raise ValueError('unsupported_local_image_format')
            else:
                dimensions = None
                extension = path.suffix.lstrip('.') or meta.get('extension') or 'bin'
                extension = re.sub(r'[^a-zA-Z0-9]', '', str(extension))[:12] or 'bin'
            decoded.append((blob,quality,extension,dimensions))
        except Exception as exc:
            errors.append(type(exc).__name__)
    if not decoded:
        return {'status':'decode_failed' if errors else 'local_missing',
                'reason':'local_copy_could_not_be_decoded' if errors else 'no_verified_local_copy',
                'error_types':sorted(set(errors))}
    best = min(ranks[value[1]] for value in decoded)
    decoded = [value for value in decoded if ranks[value[1]] == best]
    digests = {hashlib.sha256(value[0]).hexdigest() for value in decoded}
    if len(digests) != 1:
        return {'status':'ambiguous','reason':'different_local_copies_match'}
    blob,quality,extension,dimensions = decoded[0]
    dest.mkdir(parents=True,exist_ok=True)
    target = dest / _safe_name(meta.get('name'), 'attachment.'+extension)
    target.write_bytes(blob)
    result = {'status':'available','path':str(target.resolve()),'sha256':next(iter(digests)),
              'bytes':len(blob),'kind':kind}
    if kind == 'image':
        result.update(quality=quality,dimensions=dimensions)
    return result


def recover_forwarded(db, media_module, entries, dest, create_time, selector=None):
    selected = list(media_items(entries))
    if selector:
        if not re.fullmatch(r'[1-9][0-9]*(?:\.[1-9][0-9]*)*', selector):
            return {'status':'invalid_item','reason':'use_item_id_from_read'}
        selected = [item for item in selected if item['item_id'] == selector]
        if not selected:
            return {'status':'not_found','item_id':selector,'reason':'no_media_at_this_item'}
    # One bounded-account filesystem pass for the whole record, not one per image.
    files = list(_local_files(db.account_dir)) if selected else []
    result = []
    for entry in selected:
        fields = {'item_id':entry['item_id'],'sender_name':entry.get('sourcename'),
                  'time':entry.get('sourcetime'),'type':entry['media']['kind']}
        item_dest = Path(dest)/entry['item_id'].replace('.','_')
        try:
            fields.update(_recover_one(db,media_module,entry,files,item_dest,create_time))
        except Exception as exc:
            fields.update(status='error',reason='local_media_lookup_failed',error_type=type(exc).__name__)
        result.append(fields)
    if selector:
        return result[0]
    counts = collections.Counter(item['status'] for item in result)
    issues = list(record_issues(entries))
    return {'status':'forwarded','items':result,'summary':dict(total=len(result),**counts),
            'unresolved_records':issues,
            'availability':'available' if counts['available'] == len(result) and result and not issues else
                           'partial' if counts['available'] else 'unknown' if issues else
                           'local_missing' if result else 'no_media'}
