# Testing

Offline tests are the default and must have zero model and external HTTP
activity. `tests/model/` contains explicit model-required probes and is never
collected by the default `pytest.ini`. Historical superseded expectations are
preserved and exactly deselected by the offline command; they are not deleted,
silently edited or converted into fake passes.

Tests must use synthetic fixtures or approved offline evidence, never the real
production Library, jobs, media or state.
