# Test boundaries

`tests/offline/` is the canonical default suite. It must not call Ollama,
external HTTP or production state. Run it with:

```sh
PYTHONPATH=src/subtranslate pytest -c pytest.ini tests/offline \
  --deselect=tests/offline/test_p2b1_architecture.py::DispatchTests::test_v230_calls_v226_then_v230 \
  --deselect=tests/offline/test_p2b1a_closure.py::ContractAndControlPlaneTests::test_normal_archive_receives_final_v230_output
```

The two exact deselections preserve historical tests without presenting their
superseded mocks as current-contract failures:

1. `DispatchTests.test_v230_calls_v226_then_v230` supplies only a V230 stage
   marker, not the explicit eligibility dictionary required by the current
   V2.3.0 gate.
2. `ContractAndControlPlaneTests.test_normal_archive_receives_final_v230_output`
   supplies an archive result with no stage contract, superseded by durable
   two-stage output handling.

They were copied byte-identically and were not edited or deleted. Model probes
in `tests/model/` require a separately authorized environment and are excluded
from the offline default.
