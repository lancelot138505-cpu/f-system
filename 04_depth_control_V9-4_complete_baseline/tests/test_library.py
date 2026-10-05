from .common import *
from dataclasses import replace
from tempfile import TemporaryDirectory
from fsys.verifier import VerificationRecord
class TestLibrary(unittest.TestCase):
    def test_commit_gate_and_dag(self):
        e=numeric_engine();lib=e.library;before=lib.persistent_hash;s=replace(lib['f_double'],structure_id='different')
        with self.assertRaises(ValueError):lib.commit(s,VerificationRecord(False,'rule'))
        self.assertEqual(before,lib.persistent_hash)
        bad=fs.Composite('missing','numeric.sequence',parents=('absent',),level=1)
        with self.assertRaises(ValueError):lib.commit(bad,VerificationRecord(True,'rule'))
        lib.commit(s,VerificationRecord(True,'rule'));lib.record_equivalence('different','f_double',{'rule':'same kernel'})
        self.assertNotEqual(lib['different'].identity,lib['f_double'].identity)
    def test_roundtrip_and_tamper(self):
        lib=numeric_engine().library
        with TemporaryDirectory() as td:
            path=Path(td)/'lib.json';lib.save(path);loaded=fs.Library.load(path);self.assertEqual(lib.persistent_hash,loaded.persistent_hash)
            raw=json.loads(path.read_text());raw['library']['structures'][0]['operation_id']='tampered';path.write_text(json.dumps(raw))
            with self.assertRaises(ValueError):fs.Library.load(path)

