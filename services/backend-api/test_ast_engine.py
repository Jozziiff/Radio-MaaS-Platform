"""Tests for the AST analysis engine (M2). See ast_engine.py for module purpose."""

from ast_engine import analyze, find_missing_columns


def test_collects_plain_imports():
    source = "import os\nimport pandas as pd\n"

    result = analyze(source)

    assert set(result["imports"]) == {"os", "pandas"}


def test_collects_from_imports_by_top_level_module():
    source = "from collections import OrderedDict\n"

    result = analyze(source)

    assert result["imports"] == ["collections"]


def test_detects_subscript_reads_regardless_of_variable_name():
    source = (
        "df = pd.read_csv(path)\n"
        "high = df[\"load_percent\"] > 80\n"
        "for _, row in df.iterrows():\n"
        "    print(row['cell_id'])\n"
    )

    result = analyze(source)

    assert set(result["required_columns"]) == {"load_percent", "cell_id"}


def test_deduplicates_required_columns():
    source = 'a = df["cell_id"]\nb = df["cell_id"]\n'

    result = analyze(source)

    assert result["required_columns"] == ["cell_id"]


def test_excludes_assignment_targets():
    source = 'df["status"] = "ok"\n'

    result = analyze(source)

    assert result["required_columns"] == []


def test_excludes_attribute_chain_subscripts():
    source = 'input_path = os.environ["INPUT_PATH"]\n'

    result = analyze(source)

    assert result["required_columns"] == []


def test_mixed_read_write_and_attribute_access():
    source = (
        "input_path = os.environ['INPUT_PATH']\n"
        'result = df.copy()\n'
        'result["status"] = result["load_percent"].apply(f)\n'
    )

    result = analyze(source)

    # "status" is a write (Store), "INPUT_PATH" is on os.environ (Attribute,
    # not a bare Name) -- only the read of "load_percent" should count.
    assert result["required_columns"] == ["load_percent"]


def test_defaults_output_type_to_csv():
    source = "import os\n"

    result = analyze(source)

    assert result["output_type"] == "csv"


def test_never_executes_the_source():
    source = "raise RuntimeError('should never run')\n"

    result = analyze(source)

    assert result["imports"] == []
    assert result["required_columns"] == []


def test_find_missing_columns_returns_empty_when_all_present():
    assert find_missing_columns(["cell_id", "rtwp_dbm"], ["cell_id", "rtwp_dbm", "extra"]) == []


def test_find_missing_columns_returns_the_missing_ones():
    assert find_missing_columns(["cell_id", "rtwp_dbm"], ["cell_id"]) == ["rtwp_dbm"]


def test_find_missing_columns_preserves_required_order():
    assert find_missing_columns(["a", "b", "c"], []) == ["a", "b", "c"]


def test_find_missing_columns_is_case_sensitive():
    """Exact match only, no fuzzy/case-insensitive matching -- a header of
    "Cell_ID" does NOT satisfy a required column of "cell_id".
    """
    assert find_missing_columns(["cell_id"], ["Cell_ID"]) == ["cell_id"]


def test_find_missing_columns_with_no_required_columns():
    assert find_missing_columns([], ["anything"]) == []
