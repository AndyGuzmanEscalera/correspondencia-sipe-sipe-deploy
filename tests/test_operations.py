import contextlib,copy,importlib.util,io,json,os,subprocess,sys,tarfile,tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('operations',ROOT/'scripts/operations.py')
op=importlib.util.module_from_spec(spec);spec.loader.exec_module(op)
N='n1b2c3d4e5f6';M='m1b2c3d4e5f6';L='l1b2c3d4e5f6';K='k1b2c3d4e5f6';H='h1b2c3d4e5f6'
GRAPH={'heads':[N],'parents':{'g2':[],H:['g2'],'i1':['g2'],'j1':['i1'],K:['j1'],L:[K],M:[H,L],N:[M]}}
class OperationsTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='correspondencia-local-tests-');self.addCleanup(self.temp.cleanup)
  self.base=Path(self.temp.name);self.root=self.base/'checkout';self.root.mkdir()
  self.m=json.loads((ROOT/'release.json').read_text(encoding='utf-8'))
  self.m.update(status='validated',release='qa-synthetic',source_sha='a'*40,backend_digest='sha256:'+'1'*64,web_digest='sha256:'+'2'*64,platform='linux/amd64',validated_at=datetime.now(timezone.utc).isoformat())
  self.write_manifest()
 def write_manifest(self):(self.root/'release.json').write_text(json.dumps(self.m),encoding='utf-8')
 def values(self):
  v=dict(x.split('=',1) for x in (ROOT/'.env.example').read_text(encoding='utf-8').splitlines() if x and not x.startswith('#'))
  v.update(POSTGRES_DB='synthetic_db',POSTGRES_USER='synthetic_user',POSTGRES_PASSWORD='unit-db-'+'a'*32,JWT_ACCESS_SECRET='unit-jwt-'+'b'*40,PUBLIC_TRACKING_HMAC_SECRET='unit-hmac-'+'c'*40,POSTGRES_IMAGE='postgres:16-alpine@sha256:'+'3'*64,EXPECTED_DOCKER_CONTEXT='unit-context',EXPECTED_DOCKER_DAEMON_ID='unit-daemon',BACKUP_ROOT=str(self.base/'backups'),STATE_DIR=str(self.base/'state'))
  for key in ['BACKUP_ROOT','STATE_DIR']:Path(v[key]).mkdir(exist_ok=True)
  return v
 def inventory(self):
  (self.root/'.env.example').write_text('APP_ENV=production\n');names=op.versionable_files(self.root)
  (self.root/'checksums.sha256').write_text(''.join(op.sha(self.root/n)+'  '+n+'\n' for n in sorted(names)))
 def approve(self,current):
  p=op.actual_plan(GRAPH,current,N);self.m['database']={**p,'plan_approved':True};return p
 def fake(self,current=('unknown_revision',)):
  c=SimpleNamespace(root=self.root,m=copy.deepcopy(self.m),v=self.values(),deploy_sha='b'*40,allow_unestimated=False,env={'BACKEND_IMAGE':op.BACKEND+'@sha256:'+'1'*64,'WEB_IMAGE':op.WEB+'@sha256:'+'2'*64})
  c.timeout=lambda key='COMMAND_TIMEOUT_SECONDS':int(c.v[key])
  def db(sql):
   table={'SELECT 1;':'1','SELECT current_database();':c.v['POSTGRES_DB'],'SELECT current_user;':c.v['POSTGRES_USER'],'SHOW server_version_num;':'160006','SELECT pg_database_size(current_database());':'1024',"SELECT to_regclass('alembic_version') IS NOT NULL;":'t','SELECT version_num FROM alembic_version ORDER BY version_num;':'\n'.join(sorted(current))}
   return '0' if 'pg_stat_activity' in sql else table[sql]
  c.db=Mock(side_effect=db)
  pg={'Id':'pg-unit','Config':{'Labels':{'com.docker.compose.project':op.PROJECT,'com.docker.compose.service':'postgres'}},'State':{'Health':{'Status':'healthy'}},'Mounts':[{'Name':c.v['POSTGRES_VOLUME'],'Destination':'/var/lib/postgresql/data'}]}
  c.docker=Mock(side_effect=lambda *a,**k:json.dumps([pg]) if a[0]=='inspect' else '')
  def dc(*a,output=None,**k):
   if a[0]=='ps':return 'pg-unit'
   if output is not None and output!=subprocess.DEVNULL:output.write(b'synthetic dump')
   return ''
  c.dc=Mock(side_effect=dc)
  def ephemeral(image,command,output=None,**k):
   if command[-1]==op.STORAGE_CHECK:return json.dumps({'bytes':2048})
   if 'tarfile.open' in command[-1]:
    with tarfile.open(fileobj=output,mode='w:gz') as tar:
     data=b'synthetic storage';item=tarfile.TarInfo('proof.txt');item.size=len(data);tar.addfile(item,io.BytesIO(data))
    return ''
   raise AssertionError('Unexpected probe')
  c.ephemeral=Mock(side_effect=ephemeral);return c
 def test_valid_manifest_placeholder(self):self.assertEqual(op.manifest(self.root)['deploy_sha'],'')
 def test_draft_blocks_all_operations(self):
  for name in ['preflight','backup','migrate','deploy','healthcheck','rollback']:
   with self.subTest(name=name),patch.object(op.subprocess,'Popen') as process:
    with self.assertRaises(op.Refusal):op.main([name,'--apply','--writers-stopped'])
    process.assert_not_called()
 def test_bad_registry(self):
  self.m['backend_image']='untrusted/backend';self.write_manifest()
  with self.assertRaises(op.Refusal):op.manifest(self.root)
 def test_latest_rejected(self):
  self.m['backend_digest']='latest';self.write_manifest()
  with self.assertRaises(op.Refusal):op.manifest(self.root)
 def test_unknown_format(self):
  self.m['format_version']=2;self.write_manifest()
  with self.assertRaises(op.Refusal):op.manifest(self.root)
 def test_future_validation(self):
  self.m['validated_at']=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat();self.write_manifest()
  with self.assertRaises(op.Refusal):op.manifest(self.root)
 def test_complete_environment(self):op.validate_env(self.values(),self.root)
 def test_missing_secret(self):
  v=self.values();v['JWT_ACCESS_SECRET']=''
  with self.assertRaises(op.Refusal):op.validate_env(v,self.root)
 def test_short_backup_timeout(self):
  v=self.values();v['BACKUP_TIMEOUT_SECONDS']='2'
  with self.assertRaises(op.Refusal):op.validate_env(v,self.root)
 def test_external_db_rejected(self):
  v=self.values();v['DATABASE_URL']='postgresql://synthetic.invalid/db'
  with self.assertRaises(op.Refusal):op.validate_env(v,self.root)
 def test_no_shell_expansion(self):
  p=self.base/'runtime.env';p.write_text('KEY=$(not_a_command)');p.chmod(0o600)
  with self.assertRaises(op.Refusal):op.dotenv(p)
 def test_runtime_env_private(self):
  self.inventory();(self.root/'runtime.env').write_text('UNIT=private');op.verify_checksums(self.root)
 def test_private_data_excluded(self):
  self.inventory()
  for name in ['.env','.env.qa','backups/a.dump','storage/a.pdf','logs/a.log','local.dump']:
   p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('unit')
  op.verify_checksums(self.root)
 def test_worktree_git_pointer(self):
  self.inventory();(self.root/'.git').write_text('gitdir: /synthetic/unit');op.verify_checksums(self.root)
 def test_modified_versionable_file(self):
  self.inventory();(self.root/'release.json').write_text('{}')
  with self.assertRaises(op.Refusal):op.verify_checksums(self.root)
 def test_unexpected_script(self):
  self.inventory();(self.root/'unexpected.sh').write_text('exit 0')
  with self.assertRaises(op.Refusal):op.verify_checksums(self.root)
 def test_forbidden_website(self):
  self.inventory();(self.root/'website').mkdir()
  with self.assertRaises(op.Refusal):op.verify_checksums(self.root)
 def test_checksum_traversal(self):
  self.inventory();(self.root/'checksums.sha256').write_text('0'*64+'  ../outside\n')
  with self.assertRaises(op.Refusal):op.verify_checksums(self.root)
 def test_k1_n1_approved(self):
  p=self.approve([K]);self.assertIn(H,p['approved_pending_revisions']);op.approve_plan(self.m,p)
 def test_l1_n1_approved(self):
  p=self.approve([L]);self.assertEqual(set(p['approved_pending_revisions']),{H,M,N});op.approve_plan(self.m,p)
 def test_h1_l1_n1_approved(self):
  p=self.approve([H,L]);self.assertEqual(set(p['approved_pending_revisions']),{M,N});op.approve_plan(self.m,p)
 def test_m1_n1_approved(self):
  p=self.approve([M]);self.assertEqual(p['approved_pending_revisions'],[N]);op.approve_plan(self.m,p)
 def test_unknown_revision(self):
  with self.assertRaises(op.Refusal):op.actual_plan(GRAPH,['unknown'],N)
 def test_unapproved_pending(self):
  p=self.approve([L]);self.m['database']['approved_pending_revisions']=[M,N]
  with self.assertRaises(op.Refusal):op.approve_plan(self.m,p)
 def test_exact_source(self):
  self.approve([K]);p=op.actual_plan(GRAPH,[L],N)
  with self.assertRaises(op.Refusal):op.approve_plan(self.m,p)
 def test_explicit_plan_approval(self):
  p=self.approve([M]);self.m['database']['plan_approved']=False
  with self.assertRaises(op.Refusal):op.approve_plan(self.m,p)
 def test_migration_required_match(self):
  p=self.approve([M]);self.m['database']['migration_required']=False
  with self.assertRaises(op.Refusal):op.approve_plan(self.m,p)
 def test_graph_cycle(self):
  g=copy.deepcopy(GRAPH);g['parents']['g2']=[N]
  with self.assertRaises(op.Refusal):op.actual_plan(g,[M],N)
 def test_multiple_heads(self):
  g=copy.deepcopy(GRAPH);g['heads']=[N,'other']
  with self.assertRaises(op.Refusal):op.actual_plan(g,[M],N)
 def test_noop_plan(self):
  p=self.approve([N]);self.assertFalse(p['migration_required']);op.approve_plan(self.m,p)
 def test_migrate_checks_backup_and_exact_image(self):
  c=self.fake([M]);p=self.approve([M]);c.m=copy.deepcopy(self.m)
  with patch.object(op,'no_writers'),patch.object(op,'preflight_migration',return_value=p),patch.object(op,'verify_backup') as proof,patch.object(op,'raw_current',return_value=[N]):
   c.ephemeral=Mock(return_value='');op.migrate(c,self.base/'backup');proof.assert_called_once()
  self.assertIn('command.upgrade',c.ephemeral.call_args.args[1][-1]);self.assertEqual(c.ephemeral.call_args.args[0],c.env['BACKEND_IMAGE'])
 def test_backup_unknown_revision_allowed(self):
  c=self.fake(['unknown_revision'])
  with patch.object(op,'preflight_migration',side_effect=AssertionError('Do not inspect migration')),contextlib.redirect_stdout(io.StringIO()):p=op.backup(c)
  data=json.loads((p/'manifest.json').read_text());self.assertEqual(data['status'],'complete');self.assertEqual(data['alembic_current'],['unknown_revision'])
 def test_backup_db_failure(self):
  c=self.fake();original=c.dc.side_effect
  def fail(*a,**k):
   if any('pg_dump -Fc' in x for x in a):raise op.Refusal('unit dump failure')
   return original(*a,**k)
  c.dc.side_effect=fail
  with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(op.Refusal):op.backup(c)
  self.assertEqual(list(Path(c.v['BACKUP_ROOT']).rglob('manifest.json')),[])
 def test_backup_storage_failure(self):
  c=self.fake();original=c.ephemeral.side_effect
  def fail(*a,**k):
   if 'tarfile.open' in a[1][-1]:raise op.Refusal('unit storage failure')
   return original(*a,**k)
  c.ephemeral.side_effect=fail
  with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(op.Refusal):op.backup(c)
  self.assertEqual(list(Path(c.v['BACKUP_ROOT']).rglob('manifest.json')),[])
 def test_capacity_formula(self):self.assertEqual(op.capacity_required(100,200,50),650)
 def test_capacity_insufficient(self):
  c=self.fake()
  with patch.object(op.shutil,'disk_usage',return_value=SimpleNamespace(free=1)),self.assertRaises(op.Refusal):op.preflight_backup(c)
 def test_unestimated_requires_override(self):
  c=self.fake();c.ephemeral.side_effect=op.Refusal('measurement failure')
  with self.assertRaises(op.Refusal):op.preflight_backup(c)
 def test_unestimated_warning_and_floor(self):
  c=self.fake();c.allow_unestimated=True;c.ephemeral.side_effect=op.Refusal('measurement failure');out=io.StringIO()
  with patch.object(op.shutil,'disk_usage',return_value=SimpleNamespace(free=200*op.GIB)),contextlib.redirect_stderr(out),contextlib.redirect_stdout(io.StringIO()):result=op.preflight_backup(c)
  self.assertFalse(result['estimated']);self.assertIn('NO garantiza',out.getvalue())
 def test_timeout_terminates_and_sanitizes(self):
  with self.assertRaises(op.Refusal) as error:op.execute([sys.executable,'-B','-c','import time; time.sleep(2)'],dict(os.environ),timeout=0.05,label='Unit probe')
  self.assertIn('timeout',str(error.exception));self.assertNotIn('time.sleep',str(error.exception))
 def test_no_operational_asserts(self):
  import ast
  for code in [(ROOT/'scripts/operations.py').read_text(encoding='utf-8'),op.HEALTH,op.STORAGE_CHECK,op.BACKEND_CHECK,op.GRAPH,op.STORAGE_PERMISSIONS]:self.assertFalse(any(isinstance(n,ast.Assert) for n in ast.walk(ast.parse(code))))
 def optimized_health(self,bad,env_mode=False):
  harness='''import builtins,types
original=builtins.__import__
class Response:
 status=200
 headers={'Cache-Control':'no-store'}
 def __enter__(self):return self
 def __exit__(self,*a):pass
 def read(self):return b'not html' if BAD=='html' else b'<html></html>'
request=types.SimpleNamespace(urlopen=lambda *a,**k:Response(),Request=lambda *a,**k:object())
fakeurllib=types.SimpleNamespace(request=request)
fakejson=types.SimpleNamespace(load=lambda r:{'status':'error' if BAD=='db' else 'ok','database':'unreachable' if BAD=='db' else 'connected','found':BAD=='tracking'})
def fakeimport(name,*a,**k):
 if name=='urllib.request':return fakeurllib
 if name=='json':return fakejson
 return original(name,*a,**k)
b=dict(vars(builtins));b['__import__']=fakeimport
exec(CODE,{'__builtins__':b})
'''
  code='BAD='+repr(bad)+'\nCODE='+repr(op.HEALTH)+'\n'+harness
  env=dict(os.environ)
  if env_mode:env['PYTHONOPTIMIZE']='1'
  flags=[] if env_mode else ['-O']
  return subprocess.run([sys.executable,'-B']+flags+['-c',code],env=env,capture_output=True,timeout=10)
 def test_python_O_bad_html(self):self.assertNotEqual(self.optimized_health('html').returncode,0)
 def test_python_O_bad_db(self):self.assertNotEqual(self.optimized_health('db').returncode,0)
 def test_python_O_bad_tracking(self):self.assertNotEqual(self.optimized_health('tracking').returncode,0)
 def test_PYTHONOPTIMIZE_env_rejects_bad_db(self):self.assertNotEqual(self.optimized_health('db',env_mode=True).returncode,0)
 def test_python_O_valid(self):self.assertEqual(self.optimized_health('').returncode,0)
 def test_update_no_postgres_and_order(self):
  c=self.fake()
  with patch.object(op,'postgres_ready',return_value='stable-db') as ready:op.update_app(c)
  self.assertEqual(ready.call_count,3);calls=[x.args for x in c.dc.call_args_list];self.assertEqual([x[-1] for x in calls],['backend','web'])
  for a in calls:self.assertIn('--no-deps',a);self.assertIn('--no-build',a);self.assertNotIn('postgres',a)
 def test_update_unhealthy_db(self):
  c=self.fake()
  with patch.object(op,'postgres_ready',side_effect=op.Refusal('unhealthy')),self.assertRaises(op.Refusal):op.update_app(c)
  c.dc.assert_not_called()
 def test_db_identity_change_aborts(self):
  c=self.fake()
  with patch.object(op,'postgres_ready',side_effect=['before','after']),self.assertRaises(op.Refusal):op.update_app(c)
  self.assertEqual(c.dc.call_count,1)
 def test_port_free(self):op.validate_port([],True)
 def test_port_host_process_rejected(self):
  with self.assertRaises(op.Refusal):op.validate_port([],False)
 def port_container(self,project=op.PROJECT):return {'Id':'web-unit','Config':{'Labels':{'com.docker.compose.project':project,'com.docker.compose.service':'web'}},'NetworkSettings':{'Ports':{'8080/tcp':[{'HostIp':'127.0.0.1','HostPort':'8080'}]}}}
 def test_port_stack_owned(self):op.validate_port([self.port_container()],False)
 def test_port_foreign_stack(self):
  with self.assertRaises(op.Refusal):op.validate_port([self.port_container('other')],False)
 def test_daemon_arch_mismatch(self):
  with self.assertRaises(op.Refusal):op.validate_daemon({'ID':'unit','OSType':'linux','Architecture':'aarch64'},'test','test','unit','linux/amd64')
 def test_wrong_context(self):
  with self.assertRaises(op.Refusal):op.validate_daemon({'ID':'unit','OSType':'linux','Architecture':'x86_64'},'other','test','unit','linux/amd64')
 def test_arch_alias(self):op.validate_daemon({'ID':'unit','OSType':'linux','Architecture':'x86_64'},'test','test','unit','linux/amd64')
 def git_context(self,dirty=False):
  c=SimpleNamespace(root=self.root,m=self.m,v=self.values(),approved_sha=None)
  def run(a,**k):
   command=a[3:]
   if command==['rev-parse','--show-toplevel']:return str(self.root)
   if command[0]=='status':return ' M release.json' if dirty else ''
   if command[0]=='ls-files':return 'release.json\0.env.example\0'
   return 'b'*40
  c.run=Mock(side_effect=run);return c
 def test_dirty_git(self):
  with self.assertRaises(op.Refusal):op.git_state(self.git_context(True))
 def test_clean_git_records_commit(self):
  c=self.git_context();self.assertEqual(op.git_state(c),'b'*40);self.assertEqual(c.deploy_sha,'b'*40)
 def test_initial_without_images(self):
  c=self.fake();c.run=Mock(return_value=c.v['EXPECTED_DOCKER_CONTEXT'])
  def docker(*a,**k):
   if a[0]=='info':return json.dumps({'ID':c.v['EXPECTED_DOCKER_DAEMON_ID'],'OSType':'linux','Architecture':'amd64'})
   if a[0]=='compose':return '2.30.0'
   return ''
  c.docker=Mock(side_effect=docker)
  with patch.object(op.sys,'platform','linux'),patch.object(op,'git_state'),patch.object(op,'port_free',return_value=True),contextlib.redirect_stdout(io.StringIO()):op.preflight_initial(c)
  self.assertFalse(any(x.args[0]=='image' for x in c.docker.call_args_list));c.ephemeral.assert_not_called()
 def test_rollback_incompatible(self):
  c=self.fake();target=self.fake();target.m['release']='older';c.m.update(rollback_compatible=True,rollback_to='older')
  with self.assertRaises(op.Refusal):op.validate_rollback(c,target,['other_schema'])
 def test_rollback_explicit_schema_compatibility(self):
  c=self.fake();target=self.fake();target.m['release']='older';c.m.update(rollback_compatible=True,rollback_to='older',rollback_schema_compatibility={'approved':True,'current_revisions':['newer_schema'],'target_release':'older'})
  op.validate_rollback(c,target,['newer_schema'])
 def test_rollback_no_postgres_no_migration(self):
  c=self.fake([N]);target=self.fake([N]);target.m['release']='older';c.m.update(rollback_compatible=True,rollback_to='older');c.envfile=target.envfile=self.base/'private.env'
  state=Path(c.v['STATE_DIR'])/'active.json';state.write_text(json.dumps({'release':c.m,'deploy_sha':c.deploy_sha,'previous':{'release':target.m,'deploy_sha':'c'*40}}))
  with patch.object(op,'Context',return_value=target),patch.object(op,'preflight_initial'),patch.object(op,'preflight_images'),patch.object(op,'raw_current',return_value=[N]),patch.object(op,'backup'),patch.object(op,'health'),patch.object(op,'save_active'),patch.object(op,'postgres_ready',return_value='stable-db'),patch.object(op,'migrate') as migration,contextlib.redirect_stdout(io.StringIO()):op.run_rollback(c,self.root)
  migration.assert_not_called()
  for call in target.dc.call_args_list:self.assertIn('--no-deps',call.args);self.assertNotIn('postgres',call.args)
 def test_active_state_contains_deploy_sha(self):
  c=self.fake();op.save_active(c);state=json.loads((Path(c.v['STATE_DIR'])/'active.json').read_text());self.assertEqual(state['deploy_sha'],c.deploy_sha)
 def test_failed_health_stops_services(self):
  c=self.fake()
  with patch.object(op,'preflight_images'),patch.object(op,'backup',return_value=self.base/'proof'),patch.object(op,'migrate'),patch.object(op,'update_app'),patch.object(op,'health',side_effect=op.Refusal('health failed')),patch.object(op,'save_active') as active,self.assertRaises(op.Refusal):op.run_deploy(c)
  active.assert_not_called();self.assertEqual(c.dc.call_args.args,('stop','web','backend'))

 def test_generic_dependency_branch_not_omitted(self):
  g=copy.deepcopy(GRAPH);g['parents']['dependency']=['g2'];g['parents'][N].append('dependency')
  p=op.actual_plan(g,[M],N);self.assertEqual(set(p['approved_pending_revisions']),{'dependency',N})
 def test_stderr_not_disclosed(self):
  with self.assertRaises(op.Refusal) as e:op.execute([sys.executable,'-B','-c',"import sys;sys.stderr.write('synthetic-sensitive-value');sys.exit(1)"],dict(os.environ),timeout=10)
  self.assertNotIn('synthetic-sensitive-value',str(e.exception))
 def test_daemon_id_mismatch(self):
  with self.assertRaises(op.Refusal):op.validate_daemon({'ID':'foreign','OSType':'linux','Architecture':'amd64'},'test','test','expected','linux/amd64')
 def test_ephemeral_timeout_cleans_only_generated_container(self):
  c=op.Context.__new__(op.Context);calls=[]
  def docker(*a,**kw):
   calls.append(a)
   if a[0]=='run':raise op.Refusal('unit timeout')
   if a[0]=='ps':return 'container-unit'
   return ''
  c.docker=docker
  with patch.object(op.uuid,'uuid4',return_value=SimpleNamespace(hex='f'*32)),self.assertRaises(op.Refusal):c.ephemeral('unit/image',['python','-V'],timeout=60)
  self.assertIn('--read-only',calls[0]);self.assertIn('10001:10001',calls[0]);self.assertEqual(calls[-1],('rm','-f','corr-op-'+'f'*32))
 def test_posix_timeout_terminates_process_group(self):
  process=Mock(pid=1234,returncode=-15);process.communicate.side_effect=[subprocess.TimeoutExpired('unit',1),(b'',b'')]
  with patch.object(op.subprocess,'Popen',return_value=process),patch.object(op.os,'name','posix'),patch.object(op.os,'killpg',create=True) as kill,self.assertRaises(op.Refusal):op.execute(['unit-command'],{},timeout=1)
  kill.assert_called_once_with(1234,op.signal.SIGTERM)
 def test_web_probe_requires_loaded_config(self):
  self.assertIn('DUMP_INCLUDES',op.WEB_CHECK);self.assertIn('remoteip',op.WEB_CHECK)


 def test_image_preflight_checks_storage_permissions_without_writing(self):
  c=self.fake();c.ephemeral=Mock(return_value='')
  with patch.object(op,'inspect_image'),patch.object(op,'image_graph',return_value={'heads':[N]}),contextlib.redirect_stdout(io.StringIO()):op.preflight_images(c)
  probes=[call for call in c.ephemeral.call_args_list if call.args[1][-1]==op.STORAGE_PERMISSIONS]
  self.assertEqual(len(probes),1);self.assertIn('target=/app/storage',probes[0].kwargs['mounts'][0])

if __name__=='__main__':unittest.main()
