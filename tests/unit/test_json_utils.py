"""extract_json_array: fences, prose wrapping, truncation repair."""

import pytest

from app.llm.json_utils import extract_json_array

NODES = '[{"title": "One", "summary": "a", "children": []}, {"title": "Two", "summary": "b", "children": []}]'


@pytest.mark.unit
class TestExtractJsonArray:
    def test_clean_array(self):
        assert len(extract_json_array(NODES)) == 2

    def test_code_fenced(self):
        assert len(extract_json_array(f"```json\n{NODES}\n```")) == 2

    def test_prose_wrapped(self):
        text = f"Here is the outline you asked for:\n\n{NODES}\n\nLet me know!"
        assert len(extract_json_array(text)) == 2

    def test_truncated_mid_object(self):
        truncated = NODES[:-30]  # cuts into the second object
        nodes = extract_json_array(truncated)
        assert len(nodes) >= 1
        assert nodes[0]["title"] == "One"

    def test_truncated_inside_nested_children(self):
        text = (
            '[{"title": "Ch1", "summary": "s", "children": ['
            '{"title": "beat 1", "summary": "x", "children": []},'
            '{"title": "beat 2", "summ'
        )
        nodes = extract_json_array(text)
        assert nodes[0]["title"] == "Ch1"
        assert nodes[0]["children"][0]["title"] == "beat 1"

    def test_no_array_raises(self):
        with pytest.raises(ValueError):
            extract_json_array("I could not produce an outline.")

    def test_prose_containing_a_bracket_before_the_array(self):
        # The leading prose has its own '[' — must not latch onto it and fail.
        text = "Here are the beats [see below]:\n" + NODES
        assert len(extract_json_array(text)) == 2

    def test_single_object_is_one_node_not_its_children(self):
        # A lone node object → a one-element list of that node, NOT its inner
        # children array (which silently returned the wrong subset before).
        obj = '{"title": "Ch1", "summary": "s", "children": [{"title": "beat"}]}'
        nodes = extract_json_array(obj)
        assert len(nodes) == 1 and nodes[0]["title"] == "Ch1"

    def test_object_wrapping_the_array_is_unwrapped(self):
        nodes = extract_json_array('{"outline": ' + NODES + "}")
        assert len(nodes) == 2 and nodes[0]["title"] == "One"

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            extract_json_array("")
