import importlib.util,json,hashlib
from pathlib import Path
import pytest
MOD=Path(__file__).resolve().parents[1]/'native_profiles.py'
spec=importlib.util.spec_from_file_location('native_profiles_under_test',MOD);np=importlib.util.module_from_spec(spec);spec.loader.exec_module(np)
def sha(b):return hashlib.sha256(b).hexdigest()
def fixture(tmp):
 root=tmp/'skill';root.mkdir();(root/'file-adapter').mkdir()
 for folder,name in [(root,'wechat-send.dll'),(root/'file-adapter','wechat-file-send.dll')]:
  (folder/name).write_bytes(b'legacy helper');(folder/'profile.json').write_text(json.dumps({'version':'4.1.15.13','sha256':sha(b'old binary')}))
 v=root/'versions/4.2.0.13';v.mkdir(parents=True)
 for name in ['text.dll','file.dll']:(v/name).write_bytes(b'current helper')
 profile={'version':'4.2.0.13','sha256':sha(b'new binary'),'helpers':{'text':'text.dll','file':'file.dll'},'helper_sha256':{'text':sha(b'current helper'),'file':sha(b'current helper')}}
 (v/'profile.json').write_text(json.dumps(profile));dll=tmp/'4.2.0.13/Weixin.dll';dll.parent.mkdir();dll.write_bytes(b'new binary')
 return root,dll,v
@pytest.mark.parametrize('kind',['text','file'])
def test_exact_current_and_legacy_binary_selection(tmp_path,kind):
 root,dll,v=fixture(tmp_path);p,h=np.select_profile(dll,kind,root);assert p['version']=='4.2.0.13';assert h.parent==v
 dll.write_bytes(b'old binary');p,h=np.select_profile(dll,kind,root);assert p['version']=='4.1.15.13';assert 'versions' not in str(h)
def test_same_version_directory_does_not_authorize_unknown_hash(tmp_path):
 root,dll,v=fixture(tmp_path);dll.write_bytes(b'unrecognized binary')
 with pytest.raises(np.NativeProfileError) as e:np.select_profile(dll,'file',root)
 assert e.value.details['error_code']=='unsupported_wechat_binary';assert e.value.details['actual_version']=='4.2.0.13';assert not e.value.details['message_sent']
def test_changed_native_helper_is_rejected(tmp_path):
 root,dll,v=fixture(tmp_path);(v/'file.dll').write_bytes(b'changed')
 with pytest.raises(np.NativeProfileError) as e:np.select_profile(dll,'file',root)
 assert e.value.details['error_code']=='native_helper_hash_mismatch'
def test_ambiguous_binary_profiles_are_rejected(tmp_path):
 root,dll,v=fixture(tmp_path);other=root/'versions/4.2.0.99';other.mkdir();(other/'profile.json').write_bytes((v/'profile.json').read_bytes())
 with pytest.raises(np.NativeProfileError) as e:np.select_profile(dll,'text',root)
 assert e.value.details['error_code']=='ambiguous_native_profile'
def test_probe_is_bound_to_process_binary_and_helper_build():
 p={'sha256':'binary','_helper_sha256':'helper','version':'4.2.0.13','_process_created':'instance-A'};proof={'status':'object_layout_verified','pid':123,'sha256':'binary','native_sha256':'helper','process_created':'instance-A'}
 assert np.proof_matches(proof,123,p)
 for changes in [{'pid':124},{'process_created':'instance-B'},{'sha256':'other'},{'native_sha256':'rebuilt'},{'status':'submitted_unconfirmed'}]:
  with pytest.raises(np.NativeProfileError):np.require_probe(dict(proof,**changes),123,p)
 assert not np.proof_matches({'status':'object_layout_verified','pid':123,'sha256':'binary'},123,p)
def test_selection_is_read_only(tmp_path):
 root,dll,v=fixture(tmp_path);before={f.relative_to(root):f.read_bytes() for f in root.rglob('*') if f.is_file()}
 np.select_profile(dll,'text',root);after={f.relative_to(root):f.read_bytes() for f in root.rglob('*') if f.is_file()}
 assert before==after

def test_starting_a_probe_invalidates_an_old_success(tmp_path):
 p={'sha256':'binary','_helper_sha256':'helper','version':'4.2.0.13','_process_created':'instance-A'}
 old={'status':'object_layout_verified','pid':123,'sha256':'binary','native_sha256':'helper','process_created':'instance-A'}
 np.write_probe(tmp_path,old);assert np.proof_matches(json.loads((tmp_path/'runtime-probe.json').read_text()),123,p)
 np.begin_probe(tmp_path,123,p)
 with pytest.raises(np.NativeProfileError):np.require_probe(json.loads((tmp_path/'runtime-probe.json').read_text()),123,p)
 np.write_probe(tmp_path,dict(old,status='native_error'))
 with pytest.raises(np.NativeProfileError):np.require_probe(json.loads((tmp_path/'runtime-probe.json').read_text()),123,p)
 assert not list(tmp_path.glob('*.tmp'))

def fake_process(accounts,created='1.0'):
 from types import SimpleNamespace
 return lambda pid:SimpleNamespace(create_time=lambda:float(created),open_files=lambda:[SimpleNamespace(path='C:/Users/fixture/xwechat_files/'+a+'/db_storage/message/message_0.db') for a in accounts])

def test_live_process_account_must_match_reviewed_account():
 assert np.require_sender_account(123,'account_A','1.0',fake_process(['account_A']))=='account_A'
 with pytest.raises(np.NativeProfileError) as e:np.require_sender_account(123,'account_A','1.0',fake_process(['account_B']))
 assert e.value.details['error_code']=='wechat_account_changed'

@pytest.mark.parametrize('accounts',[[],['account_A','account_B']])
def test_unresolved_or_multiple_accounts_are_rejected(accounts):
 with pytest.raises(np.NativeProfileError) as e:np.require_sender_account(123,'account_A','1.0',fake_process(accounts))
 assert e.value.details['error_code']=='wechat_account_unresolved'

def test_reused_pid_does_not_pass_process_identity():
 with pytest.raises(np.NativeProfileError) as e:np.require_sender_account(123,'account_A','1.0',fake_process(['account_A'],'2.0'))
 assert e.value.details['error_code']=='wechat_process_changed'
