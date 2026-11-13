import importlib.util,json,sys,subprocess
from pathlib import Path
import pytest
SEND=Path(__file__).resolve().parents[1]
SCRIPTS=SEND.parent
sys.path.insert(0,str(SEND));sys.path.insert(0,str(SCRIPTS))
def load(name,file):
 spec=importlib.util.spec_from_file_location(name,file);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
@pytest.mark.parametrize('kind',['text','file'])
def test_send_without_review_never_selects_a_wechat_process(tmp_path,monkeypatch,kind):
 adapter=load('guard_'+kind,SEND/('adapter.py' if kind=='text' else 'file-adapter/adapter.py'))
 def forbidden(*a,**k):raise AssertionError('Process selection must not run without approval')
 monkeypatch.setattr(adapter,'select',forbidden)
 argv=['adapter.py','send','--to','filehelper','--text','offline guard fixture']
 if kind=='file':
  f=tmp_path/'fixture.txt';f.write_text('offline guard fixture')
  argv=['adapter.py','send-file','--to','filehelper','--file',str(f)]
 monkeypatch.setattr(sys,'argv',argv)
 with pytest.raises(RuntimeError,match='exact draft ID'):adapter.main()
def test_pending_review_cannot_authorize_send(tmp_path,monkeypatch):
 import review
 monkeypatch.setattr(review,'REVIEW_ROOT',tmp_path/'reviews');monkeypatch.setattr(review,'protect_private',lambda:None)
 draft=review.create_draft('text','fixture_recipient','fixture','fixture_account',123,text='offline fixture')
 assert draft['status']=='pending_review'
 with pytest.raises(RuntimeError,match='Human approval required'):review.validate_review(draft['draft_id'],'text','fixture_recipient',pid=123,raw=b'offline fixture')
def test_changed_review_snapshot_is_rejected(tmp_path,monkeypatch):
 import review
 monkeypatch.setattr(review,'REVIEW_ROOT',tmp_path/'reviews');monkeypatch.setattr(review,'protect_private',lambda:None)
 source=tmp_path/'fixture.txt';source.write_text('offline original')
 draft=review.create_draft('file','fixture_recipient','fixture','fixture_account',123,file=source)
 Path(draft['payload']['review_file']).write_text('offline modified')
 with pytest.raises(RuntimeError,match='Reviewed file changed'):review.load_draft(draft['draft_id'])
def test_health_only_calls_inspect_with_hidden_windows(monkeypatch):
 wx=load('wechat_health_under_test',SCRIPTS/'wechat.py');calls=[]
 def fake_run(args,**kwargs):
  calls.append((args,kwargs));return subprocess.CompletedProcess(args,1,json.dumps({'status':'error','error_code':'unsupported_wechat_binary','message_sent':False}), '')
 monkeypatch.setattr(wx.subprocess,'run',fake_run);health=wx.native_send_health()
 assert set(health)=={'text','file'};assert len(calls)==2
 assert all(args[-1]=='inspect' and k['creationflags']==subprocess.CREATE_NO_WINDOW for args,k in calls)
 assert all(v['error_code']=='unsupported_wechat_binary' and v['message_sent'] is False for v in health.values())
def test_health_failure_does_not_raise_into_database_readiness(monkeypatch):
 wx=load('wechat_health_timeout',SCRIPTS/'wechat.py')
 def fail(args,**kwargs):raise subprocess.TimeoutExpired(args,20)
 monkeypatch.setattr(wx.subprocess,'run',fail)
 assert all(v['status']=='unavailable' for v in wx.native_send_health().values())
