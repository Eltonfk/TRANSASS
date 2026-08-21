import ast, inspect, json, os, tempfile, threading, unittest
from pathlib import Path
from unittest.mock import patch

from src.subtranslate.recovery_guard.core import *
from tests.offline.guard_fixtures import structured_tool_contract

REAL_ROOT = Path('/home/palhacinho/.local/state/subtranslate/recovery-apply-capabilities')

class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name) / 'state'; self.root.mkdir()
        self.store = StateStore(self.root); self.auth = FixtureAuthenticator(os.urandom(32))
        interpreter = Path(self.tmp.name) / 'python3'; interpreter.write_bytes(b'fixture-python')
        resolved, interpreter_sha = fixture_python_identity(interpreter)
        self.bindings = ExpectedBindings('/fixture/ledger.json','target','snapshot','toolchain','executor',fixed_argv_identity(),resolved,interpreter_sha)
        self.issuer = CapabilityIssuer(self.store,self.auth); self.calls=[]
        self.broker = Broker(self.store,self.auth,self.bindings,lambda argv, env: self.calls.append((argv,env)) is None)
    def tearDown(self): self.tmp.cleanup()
    def issue(self, **kw):
        expires_at = kw.pop('expires_at', None)
        return self.issuer.issue(ExpectedBindings(**{**self.bindings.__dict__,**kw}), expires_at=expires_at)
    def terminal(self): return list(self.store.folder('terminal').glob('*.json'))
    def test_schema_nonce_and_modes(self):
        a,b=self.issue(),self.issuer.issue if False else None
        self.assertEqual(a['schema_version'],SCHEMA_VERSION); self.assertEqual(a['max_uses'],1); self.assertEqual(a['uses'],0); self.assertGreaterEqual(len(a['nonce']),32)
        self.assertEqual(self.store.path('armed',a['capability_id']).stat().st_mode & 0o777,0o600); self.assertEqual(self.root.stat().st_mode & 0o777,0o700)
    def test_duplicate_and_zero_arg_contract(self):
        self.issue();
        with self.assertRaisesRegex(CapabilityError,'DUPLICATE'): self.issue()
        with self.assertRaisesRegex(CapabilityError,'ZERO_ARGUMENT'): self.broker.execute_zero_args({'path':'x'})
    def test_concurrent_issue_has_one_winner(self):
        outcomes=[]
        def issue():
            try: outcomes.append(self.issuer.issue(self.bindings)['capability_id'])
            except CapabilityError as exc: outcomes.append(str(exc))
        ts=[threading.Thread(target=issue) for _ in range(8)]; [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(sum(1 for x in outcomes if len(x)==64),1); self.assertEqual(len(list(self.store.folder('armed').glob('*.json'))),1)
    def test_fixed_runner_and_environment(self):
        self.issue(); self.assertEqual(self.broker.execute_zero_args(),'SUCCEEDED'); self.assertEqual(self.calls[0][0],FIXED_ARGV); self.assertEqual(self.calls[0][1],MINIMAL_ENV); self.assertNotIn('PYTHONPATH',self.calls[0][1])
    def test_no_arm_and_rearm_surface(self):
        self.assertFalse(hasattr(self.broker,'issue')); self.assertFalse(hasattr(self.broker,'arm')); self.assertFalse(hasattr(self.broker,'rearm'))
        self.assertEqual(structured_tool_contract.ARGUMENTS,{})
        self.issue()
        self.assertEqual(structured_tool_contract.invoke(self.broker,{}), 'SUCCEEDED')
        self.assertFalse(hasattr(structured_tool_contract,'issue')); self.assertFalse(hasattr(structured_tool_contract,'rearm'))
    def test_concurrent_claim_exactly_one(self):
        self.issue(); outcomes=[]
        def call():
            try: outcomes.append(self.broker.execute_zero_args())
            except CapabilityError as e: outcomes.append(str(e))
        ts=[threading.Thread(target=call) for _ in range(8)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(outcomes.count('SUCCEEDED'),1); self.assertEqual(len(self.calls),1); self.assertEqual(len(list(self.store.folder('armed').glob('*'))),0)
    def test_replay_all_terminal_states(self):
        for fault in (None,'after_claim','after_start','after_exit'):
            with self.subTest(fault=fault):
                self.issue()
                try: self.broker.execute_zero_args(fault=fault)
                except CapabilityError: pass
                with self.assertRaisesRegex(CapabilityError,'NOT_ARMED'): self.broker.execute_zero_args()
                self.assertTrue(self.terminal()); [p.unlink() for p in self.terminal()]
    def test_abandoned_claim_is_reconciled_to_unknown(self):
        cap=self.issue(); armed=self.store.path('armed',cap['capability_id']); claimed=self.store.path('claimed',cap['capability_id'])
        os.replace(armed,claimed)
        with self.assertRaisesRegex(CapabilityError,'NOT_ARMED'): self.broker.execute_zero_args()
        doc=json.loads(self.terminal()[0].read_text())
        self.assertEqual(doc['state'],'CLAIMED_EXECUTION_STATE_UNKNOWN'); self.assertEqual(self.calls,[])
    def test_failure_is_terminal(self):
        self.broker.runner=lambda *_:False; self.issue(); self.assertEqual(self.broker.execute_zero_args(),'FAILED')
        with self.assertRaises(CapabilityError): self.broker.execute_zero_args()
    def test_stale_bindings_and_expiry(self):
        fields=('snapshot_fingerprint','execution_toolchain_fingerprint','executor_sha256','target_prewrite_sha256','operation_id','family_id','episode_id','fixed_argv_identity','interpreter_sha256','interpreter_resolved_path')
        for field in fields:
            with self.subTest(field=field):
                self.issue(); changed=ExpectedBindings(**{**self.bindings.__dict__,field:'changed'}); self.broker.bindings=changed
                with self.assertRaisesRegex(CapabilityError,'STALE'): self.broker.execute_zero_args()
                self.assertTrue(self.terminal()); [p.unlink() for p in self.terminal()]; self.broker.bindings=self.bindings
        self.issue(expires_at=0)
        with self.assertRaisesRegex(CapabilityError,'EXPIRED'): self.broker.execute_zero_args()
    def test_tamper_and_forgery_rejected(self):
        cap=self.issue(); p=self.store.path('armed',cap['capability_id']); data=json.loads(p.read_text()); data['target_path']='evil'; p.write_text(json.dumps(data))
        with self.assertRaisesRegex(CapabilityError,'AUTHENTICITY'): self.broker.execute_zero_args()
    def test_schema_invalid_even_when_fixture_authority_resigns(self):
        cap=self.issue(); p=self.store.path('armed',cap['capability_id']); data=json.loads(p.read_text()); data['schema_version']='other'; data['authenticity']['mac']=self.auth.sign(data); p.write_text(json.dumps(data))
        with self.assertRaisesRegex(CapabilityError,'SCHEMA'): self.broker.execute_zero_args()
    def test_journal_order_and_faults_fail_closed(self):
        self.issue(); self.broker.execute_zero_args(); events=[json.loads(x)['event'] for x in self.store.path('journal',next(self.store.folder('journal').glob('*.jsonl')).stem).with_suffix('.jsonl').read_text().splitlines()]
        self.assertEqual(events,['ISSUED','CLAIMED','EXECUTOR_STARTED','EXECUTOR_EXITED','SUCCEEDED'])
    def test_claim_journal_and_rename_fail_before_runner(self):
        self.issue(); original=self.store.append_journal
        def fail_claim(event, document):
            if event == 'CLAIMED': raise OSError('fixture journal failure')
            return original(event, document)
        self.store.append_journal=fail_claim
        with self.assertRaises(CapabilityError): self.broker.execute_zero_args()
        self.assertEqual(self.calls,[]); self.assertEqual(len(list(self.store.folder('armed').glob('*'))),0)
        self.store.append_journal=original; self.issue()
        with patch('src.subtranslate.recovery_guard.core.os.replace', side_effect=OSError('rename failure')):
            with self.assertRaises(OSError): self.broker.execute_zero_args()
        self.assertEqual(self.calls,[]); self.assertEqual(len(list(self.store.folder('armed').glob('*'))),1)
    def test_core_has_no_real_executor_or_opencode_surface(self):
        import src.subtranslate.recovery_guard.core as core
        source=inspect.getsource(core)
        imports=[node.names[0].name for node in ast.walk(ast.parse(source)) if isinstance(node,ast.Import) and node.names]
        self.assertNotIn('subprocess',imports)
        self.assertFalse(REAL_ROOT.exists())
    def test_symlink_and_real_root_absent(self):
        self.assertFalse(REAL_ROOT.exists()); self.issue(); cap=next(self.store.folder('armed').glob('*.json')); cap.unlink(); cap.symlink_to(self.root/'nope')
        with self.assertRaises(CapabilityError): self.broker.execute_zero_args()
    def test_symlink_root_and_fixed_argv_identity(self):
        link=Path(self.tmp.name)/'link'; link.symlink_to(self.root)
        with self.assertRaisesRegex(CapabilityError,'ROOT'): StateStore(link)
        self.assertEqual(self.bindings.fixed_argv_identity,fixed_argv_identity())

if __name__ == '__main__': unittest.main()
