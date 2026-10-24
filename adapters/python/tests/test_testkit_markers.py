from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parents[1] / 'hami-testkit/src'))

from dataclasses import dataclass

import pytest

from hami_testkit.markers import definition_nodeid, marker_contract_violations



@dataclass(frozen=True)
class _Item:
    nodeid: str
    markers: frozenset[str]
    originalname: str | None = None

    def get_closest_marker(self, name: str) -> object | None:
        return object() if name in self.markers else None


def test_slow_requires_a_semantic_marker():
    item = _Item("tests/test_example.py::test_case", frozenset({"slow"}))

    assert marker_contract_violations([item]) == [
        "tests/test_example.py::test_case: slow requires one semantic marker"
    ]


def test_slow_can_overlay_a_semantic_marker():
    item = _Item("tests/test_example.py::test_case", frozenset({"integration", "slow"}))

    assert marker_contract_violations([item]) == []


def test_semantic_markers_are_mutually_exclusive():
    item = _Item("tests/test_example.py::test_case", frozenset({"integration", "statistical"}))

    assert marker_contract_violations([item]) == [
        "tests/test_example.py::test_case: multiple semantic markers: integration, statistical"
    ]


def test_grandfathered_unmarked_definition_is_allowed_for_every_parameter():
    item = _Item(
        "tests/test_example.py::test_case[value-a]", frozenset(), originalname="test_case"
    )

    assert definition_nodeid(item) == "tests/test_example.py::test_case"
    assert marker_contract_violations(
        [item], allowed_unmarked=frozenset({"tests/test_example.py::test_case"})
    ) == []


def test_definition_nodeid_handles_ipv6_parameter_ids():
    item = _Item(
        "tests/test_example.py::test_case[::1]", frozenset(), originalname="test_case"
    )

    assert definition_nodeid(item) == "tests/test_example.py::test_case"


def test_new_unmarked_definition_is_rejected():
    item = _Item("tests/test_example.py::test_new", frozenset(), originalname="test_new")

    assert marker_contract_violations([item]) == [
        "tests/test_example.py::test_new: unmarked test is not in the legacy baseline"
    ]
