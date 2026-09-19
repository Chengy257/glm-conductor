SHARED-CONFLICT-BASELINE

Fixture for WF-11 (conflicting parallel writes). Two probe agents write incompatible single-line
contents (`ALPHA-CONTENT-...` and `BETA-CONTENT-...`) to the run-local copy of this file at the
same time. The committed baseline records the pre-experiment content so the final winner can be
identified unambiguously.
