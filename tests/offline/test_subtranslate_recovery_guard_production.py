import ast
import hashlib
import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.subtranslate.recovery_guard.production.bindings import REQUIRED_BINDINGS, validate_bindings
from src.subtranslate.recovery_guard.production.broker import BrokerError, ProductionBroker
from src.subtranslate.recovery_guard.production.crypto import Ed25519Verifier, public_key_id
from src.subtranslate.recovery_guard.production.issuer import ExternalIssuer, issuer_main
from src.subtranslate.recovery_guard.production.issuer_cli import main as issuer_cli_main
from src.subtranslate.recovery_guard.production.manifest import MANIFEST_SCHEMA_VERSION, manifest_fingerprint
from src.subtranslate.recovery_guard.production.runner import FixedRunner, FUTURE_INTERPRETER
from src.subtranslate.recovery_guard.production.schema import canonical_payload
from src.subtranslate.recovery_guard.production.service import REQUEST, UnixBrokerService, ServiceError, activated_listener
from src.subtranslate.recovery_guard.production.service_main import main as service_main
from src.subtranslate.recovery_guard.production.state import ProductionStateStore, StateError
from src.subtranslate.recovery_guard.production.provider import BindingProviderError, PhysicalBindingProvider
from src.subtranslate.recovery_guard.production.manifest import ManifestError, validate_manifest

EXECUTOR = Path('.opencode/tools/subtranslate_recovery_ledger_reprepare.py')
DURABILITY = Path('src/subtranslate/v238_per_call_durability.py')


class Result:
    def __init__(self, code=0): self.returncode = code


class ProductionGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'state'; self.root.mkdir()
        self.store = ProductionStateStore(self.root)
        self.private = Ed25519PrivateKey.generate()
        self.key_id = public_key_id(self.private.public_key())
        self.bindings = {
            'operation_id': 'SUBTRANSLATE_V238_E07_R6C_B4_RECOVERY_20260818T165144Z', 'family_id': 'V238_E07_R6C_B4_RECOVERY',
            'episode_id': '79', 'target_path': '/fixture/ledger.json', 'target_prewrite_sha256': 'target',
            'snapshot_fingerprint': 'snapshot', 'execution_toolchain_fingerprint': 'toolchain',
            'executor_id': 'RECOVERY_LEDGER_REPREPARATION_V2', 'executor_sha256': 'executor', 'durability_sha256': 'durability',
            'bundle_manifest_fingerprint': 'bundle', 'expected_blocker': 'RECOVERY_LEDGER_IDENTITY_SCHEMA_INCOMPLETE', 'fixed_argv_identity': 'argv', 'python_interpreter_identity': 'python',
            'public_key_id': self.key_id, 'authorization_policy_version': 'policy-1', 'arming_authority': 'root-external-issuer',
        }
        validate_bindings(self.bindings)
        self.calls = []
        self.issuer = ExternalIssuer(self.store, self.private, lambda: dict(self.bindings))
        self.broker = ProductionBroker(self.store, Ed25519Verifier(self.private.public_key(), self.key_id), lambda: dict(self.bindings), lambda: self.calls.append(1) is None)

    def tearDown(self): self.tmp.cleanup()
    def issue(self): return self.issuer.issue_fixed_action()

    def test_canonical_ed25519_and_immutable_payload(self):
        document = self.issue(); payload = document['payload']; before = canonical_payload(payload)
        self.assertEqual(before, canonical_payload(dict(reversed(list(payload.items())))))
        self.assertEqual(self.broker.execute_fixed_request(REQUEST), 'SUCCEEDED')
        terminal = next((self.root/'terminal').glob('*.json'))
        self.assertEqual(json.loads(terminal.read_text())['payload'], payload)
        self.assertEqual(canonical_payload(payload), before)
        changed = dict(payload); changed['target_path'] = 'changed'
        with self.assertRaises(Exception): self.broker.verifier.verify(changed, __import__('base64').b64decode(document['signature']))

    def test_bad_signature_key_schema_expiry_and_stale_are_blocked(self):
        for mutate in ('signature', 'key', 'keyid', 'field', 'expiry', 'snapshot', 'manifest', 'target', 'operation', 'family', 'episode'):
            with self.subTest(mutate=mutate):
                document = self.issue(); path = next((self.root/'armed').glob('*.json')); data = json.loads(path.read_text())
                if mutate == 'signature': data['signature'] = 'x' * 88
                elif mutate == 'key':
                    other = Ed25519PrivateKey.generate().public_key(); self.broker.verifier = Ed25519Verifier(other, public_key_id(other))
                elif mutate == 'keyid': data['payload']['public_key_id'] = 'ed25519-sha256:bad'
                elif mutate == 'field': data['payload']['unexpected'] = 'x'
                elif mutate == 'expiry': data['payload']['expires_at'] = 0
                else: self.broker.binding_provider = lambda m=mutate: {**self.bindings, {'snapshot':'snapshot_fingerprint','manifest':'bundle_manifest_fingerprint','target':'target_prewrite_sha256','operation':'operation_id','family':'family_id','episode':'episode_id'}[mutate]: 'changed'}
                if mutate in {'signature','keyid','field','expiry'}: path.write_text(json.dumps(data))
                with self.assertRaises(BrokerError): self.broker.execute_fixed_request(REQUEST)
                self.assertEqual(self.calls, [])
                self.assertTrue(list((self.root/'terminal').glob('*.json')))
                for file in (self.root/'terminal').glob('*'): file.unlink()
                self.broker.binding_provider = lambda: dict(self.bindings)
                self.broker.verifier = Ed25519Verifier(self.private.public_key(), self.key_id)

    def test_concurrency_replay_multiple_and_faults(self):
        self.issue(); outcome=[]
        def call():
            try: outcome.append(self.broker.execute_fixed_request(REQUEST))
            except Exception as exc: outcome.append(type(exc).__name__)
        threads=[threading.Thread(target=call) for _ in range(8)]
        [thread.start() for thread in threads]; [thread.join() for thread in threads]
        self.assertEqual(outcome.count('SUCCEEDED'), 1); self.assertEqual(len(self.calls), 1)
        with self.assertRaises(BrokerError): self.broker.execute_fixed_request(REQUEST)
        self.assertEqual(len(self.calls), 1)

    def test_socket_rejects_nonfixed_request_and_peer_is_checked(self):
        self.issue(); left, right = socket.socketpair()
        try:
            service = UnixBrokerService(self.broker, os.getuid())
            class Listener:
                def accept(self): return left, None
            thread = threading.Thread(target=service.serve_once, args=(Listener(),)); thread.start(); right.sendall(b'{}\n'); right.shutdown(socket.SHUT_WR); self.assertIn(b'DENY', right.recv(256)); thread.join()
        finally: right.close()

    def test_runner_manifest_and_source_separation(self):
        bundle = Path(self.tmp.name) / 'bundle'; (bundle/'.opencode/tools').mkdir(parents=True); target=bundle/'.opencode/tools/subtranslate_recovery_ledger_reprepare.py'; target.write_text('fixture')
        roles = {
            'broker': 'src/broker.py', 'capability_schema': 'src/schema.py', 'crypto_verifier': 'src/crypto.py',
            'state': 'src/state.py', 'journal': 'src/journal.py', 'binding_provider': 'src/provider.py',
            'runner': 'src/runner.py', 'protocol': 'src/protocol.py', 'executor': '.opencode/tools/subtranslate_recovery_ledger_reprepare_v2.py',
            'durability': 'src/subtranslate/v238_per_call_durability.py', 'structured_tool': 'opencode/tool.ts',
            'systemd_service': 'systemd/service.unit', 'systemd_socket': 'systemd/socket.unit', 'service_entrypoint': 'bin/service.py',
            'interpreter': 'manifest/interpreter.identity', 'issuer': 'src/issuer.py',
        }
        for role, relative in roles.items():
            path = bundle / relative; path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists(): path.write_text(role)
        components = {relative: hashlib.sha256((bundle / relative).read_bytes()).hexdigest() for relative in roles.values()}
        interpreter_sha = hashlib.sha256(Path(FUTURE_INTERPRETER).read_bytes()).hexdigest()
        argv_identity = hashlib.sha256(json.dumps([FUTURE_INTERPRETER, '.opencode/tools/subtranslate_recovery_ledger_reprepare.py', '--apply'], separators=(',', ':')).encode()).hexdigest()
        manifest = {'schema_version': MANIFEST_SCHEMA_VERSION, 'source_git': 'sha1:oid', 'source_tree':'sha1:tree',
            'components': components, 'dependency_list':list(components), 'component_roles': roles,
            'executor_id':'RECOVERY_LEDGER_REPREPARATION_V2', 'executor_sha256':components[roles['executor']], 'durability_sha256':components[roles['durability']],
            'interpreter':{'declared_path':FUTURE_INTERPRETER,'resolved_path':FUTURE_INTERPRETER,'sha256':interpreter_sha}, 'public_key_id':self.key_id,
            'fixed_action_id':'RECOVERY_LEDGER_REPREPARATION','fixed_argv_identity':hashlib.sha256(json.dumps(['/usr/bin/python3.12','-I','-B','.opencode/tools/subtranslate_recovery_ledger_reprepare_v2.py','--apply'], separators=(',', ':')).encode()).hexdigest(),'socket_policy':'fixed','state_root_policy':'fixed','target_policy':'fixed','backup_policy':'/var/lib/subtranslate-guard/backups','uid_gid_policy':'fixed',
            'unit_hashes':{'service':components[roles['systemd_service']], 'socket':components[roles['systemd_socket']]},
            'broker_sha256':components[roles['broker']], 'issuer_sha256':components[roles['issuer']], 'structured_tool_sha256':components[roles['structured_tool']]}
        manifest['manifest_fingerprint']=manifest_fingerprint(manifest); calls=[]
        self.assertTrue(FixedRunner(bundle, manifest, lambda *args, **kw: calls.append((args,kw)) or Result()).run())
        self.assertEqual(calls[0][0][0][1:3], ('-I', '-B')); self.assertEqual(calls[0][0][0][4], '--apply'); self.assertFalse(calls[0][1]['shell']); self.assertNotIn('PYTHONPATH', calls[0][1]['env'])
        source = Path('src/subtranslate/recovery_guard/production/broker.py').read_text()
        self.assertNotIn('issuer import', source); self.assertNotIn('Ed25519PrivateKey', source)

    def test_issuer_cli_and_bundle_closure_are_fail_closed(self):
        with self.assertRaises(Exception): issuer_main(['extra'], lambda: 0)
        with self.assertRaises(Exception): issuer_main([], lambda: 1000)
        with self.assertRaises(Exception): issuer_cli_main(['extra'], geteuid=lambda: 0)
        with self.assertRaises(Exception): issuer_cli_main([], geteuid=lambda: 0)
        executor = EXECUTOR.read_text(); self.assertIn('CANDIDATE_ROOT / "src"', executor)
        self.assertEqual(hashlib.sha256(EXECUTOR.read_bytes()).hexdigest(), '2f0fc420399671f06040a46405d42eca532c692d0b62729353fb90b840a04801')
        self.assertEqual(hashlib.sha256(DURABILITY.read_bytes()).hexdigest(), '5caeb33f1bb21fbc90b7195b791e061bc46a7bddedb49bb15f52908b09d23585')

    def test_service_entrypoint_is_fixed_and_unconfigured_by_default(self):
        with self.assertRaises(ServiceError): service_main(['extra'])
        with self.assertRaises(ServiceError): service_main([])

    def test_no_real_roots_or_private_key_paths(self):
        self.assertFalse(Path('/var/lib/subtranslate-guard').exists())
        self.assertFalse(Path('/etc/subtranslate-guard/keys/issuer.ed25519').exists())

    def test_capability_id_is_validated_before_path_derivation(self):
        for bad in ('', '.', '..', '../escape', 'A' * 64, 'f' * 63, 'f' * 65, 'f' * 63 + '/', 'f' * 32 + '\\' + 'f' * 31):
            with self.subTest(bad=bad):
                with self.assertRaises(StateError): self.store.path('armed', bad)
                with self.assertRaises(StateError): self.store.lock(bad)

    def test_socket_framing_rejects_partial_extra_and_second_frame(self):
        for body in (REQUEST[:-1], REQUEST + b'X', REQUEST + REQUEST, b''):
            with self.subTest(body=body):
                left, right = socket.socketpair()
                try:
                    service = UnixBrokerService(self.broker, os.getuid())
                    class Listener:
                        def accept(self): return left, None
                    thread = threading.Thread(target=service.serve_once, args=(Listener(),)); thread.start()
                    right.sendall(body); right.shutdown(socket.SHUT_WR)
                    self.assertIn(b'DENY', right.recv(256)); thread.join()
                finally: right.close()

    def test_socket_activation_env_is_fail_closed(self):
        for env in ({'LISTEN_PID': str(os.getpid()), 'LISTEN_FDS': '0'}, {'LISTEN_PID': str(os.getpid()), 'LISTEN_FDS': '2'}, {'LISTEN_PID': '1', 'LISTEN_FDS': '1'}, {'LISTEN_PID': 'bad', 'LISTEN_FDS': '1'}):
            with self.subTest(env=env):
                with self.assertRaises(ServiceError): activated_listener(env, fromfd=lambda *_: None)

    def test_physical_provider_fails_closed_on_incomplete_identity(self):
        provider = PhysicalBindingProvider(bundle_manifest_fingerprint='0' * 64, public_key_id=self.key_id,
                                           run_probe=lambda: {'blockers': [{'code': 'RECOVERY_LEDGER_IDENTITY_SCHEMA_INCOMPLETE'}], 'runtime': {'operation': {}, 'episode_budget': {}}})
        with self.assertRaises(BindingProviderError): provider.measure()

    def test_manifest_traversal_and_hash_fail_closed(self):
        manifest = {'schema_version': MANIFEST_SCHEMA_VERSION, 'source_git':'sha1:x', 'source_tree':'sha1:y',
            'components': {'../escape':'0'*64}, 'dependency_list':['../escape'], 'executor_sha256':'0'*64, 'durability_sha256':'0'*64,
            'interpreter': {'declared_path':'/usr/bin/python3.12','resolved_path':'/usr/bin/python3.12','sha256':'0'*64}, 'public_key_id':'k',
            'fixed_action_id':'RECOVERY_LEDGER_REPREPARATION','fixed_argv_identity':'a','socket_policy':'s','state_root_policy':'s','uid_gid_policy':'u',
            'unit_hashes':{},'broker_sha256':'b','issuer_sha256':'i','structured_tool_sha256':'t'}
        manifest['manifest_fingerprint'] = manifest_fingerprint(manifest)
        with self.assertRaises(ManifestError): validate_manifest(manifest, Path(self.tmp.name))

    def test_journal_failure_after_claim_is_terminal_unknown(self):
        self.issue(); original = self.store.append_event
        def fail(event, payload, state):
            if event == 'CLAIMED': raise OSError('fsync failure')
            return original(event, payload, state)
        self.store.append_event = fail
        with self.assertRaises(BrokerError): self.broker.execute_fixed_request(REQUEST)
        self.assertEqual(len(self.calls), 0); self.assertEqual(len(list((self.root/'claimed').glob('*.json'))), 0)
        self.assertTrue(list((self.root/'terminal').glob('*.json')))

    def test_claimed_filename_is_validated_before_reconciliation(self):
        bad = self.root / 'claimed' / '../escape.json'
        # The lexical escape is never accepted as a state path.
        with self.assertRaises(StateError): self.store.read_document(bad)

if __name__ == '__main__': unittest.main()
