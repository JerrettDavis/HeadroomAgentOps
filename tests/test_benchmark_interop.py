import json

import pytest

from agentops.benchmarks.cli import main
from agentops.benchmarks.interop import export_dataset, import_inspect


def taskset():
    return {
        "taskset_id": "shared",
        "revision": "1",
        "tasks": [
            {
                "id": "sum",
                "messages": [{"role": "user", "content": "19+23?"}],
                "expected": "42",
                "max_tokens": 32,
                "stream": True,
            }
        ],
    }


def test_inspect_roundtrip_preserves_native_controls():
    source = taskset()
    rows = export_dataset(source, "inspect")
    assert rows[0]["input"] == source["tasks"][0]["messages"]
    assert rows[0]["target"] == "42"
    assert import_inspect(rows, "shared", "1") == source


def test_import_plain_inspect_samples():
    result = import_inspect(
        [{"id": 7, "input": "Question?", "target": "Answer"}], "suite", "r1"
    )
    assert result["tasks"][0] == {
        "id": "7",
        "messages": [{"role": "user", "content": "Question?"}],
        "expected": "Answer",
    }


@pytest.mark.parametrize(
    "change",
    [
        {"choices": ["a", "b"]},
        {"setup": "run me"},
        {"files": {"x": "y"}},
        {"target": ["a", "b"]},
        {"target": ""},
        {"input": [{"role": "user", "content": []}]},
        {"metadata": {"grading": "custom"}},
        {"metadata": {"headroom": {"stream": "yes"}}},
    ],
)
def test_inspect_import_rejects_ambiguous_or_execution_bearing_samples(change):
    with pytest.raises(ValueError):
        import_inspect(
            [{"id": "x", "input": "q", "target": "a"} | change], "suite", "1"
        )


def test_import_requires_stable_unique_ids():
    for rows in (
        [{"input": "q", "target": "a"}],
        [{"id": "x", "input": "q", "target": "a"}] * 2,
    ):
        with pytest.raises(ValueError):
            import_inspect(rows, "suite", "1")


def test_aiperf_single_turn_export():
    assert export_dataset(taskset(), "aiperf") == [
        {"text": "19+23?", "output_length": 32}
    ]


def test_real_aiperf_loader_preserves_prompt_and_output_budget(tmp_path):
    pytest.importorskip("aiperf")
    from aiperf.dataset.loader.single_turn import SingleTurnDatasetLoader

    path = tmp_path / "workload.jsonl"
    rows = export_dataset(taskset(), "aiperf")
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    loader = SingleTurnDatasetLoader(filename=path)
    conversations = loader.convert_to_conversations(loader.load_dataset())
    assert len(conversations) == 1
    assert conversations[0].turns[0].texts[0].contents == ["19+23?"]
    assert conversations[0].turns[0].max_tokens == 32


def test_aiperf_does_not_flatten_conversation():
    source = taskset()
    source["tasks"][0]["messages"].insert(0, {"role": "system", "content": "Rules"})
    with pytest.raises(ValueError, match="single user"):
        export_dataset(source, "aiperf")


@pytest.mark.parametrize("format", ["inspect", "aiperf"])
def test_export_does_not_silently_drop_tools(format):
    source = taskset()
    source["tasks"][0]["tools"] = []
    with pytest.raises(ValueError):
        export_dataset(source, format)


def test_cli_dataset_interchange(tmp_path):
    source, output, restored = (
        tmp_path / n for n in ("tasks.json", "inspect.jsonl", "restored.json")
    )
    source.write_text(json.dumps(taskset()), encoding="utf-8")
    assert (
        main(
            [
                "export-dataset",
                str(source),
                "--format",
                "inspect",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "import-inspect",
                str(output),
                "--taskset-id",
                "shared",
                "--revision",
                "1",
                "--output",
                str(restored),
            ]
        )
        == 0
    )
    assert json.loads(restored.read_text()) == taskset()


@pytest.mark.parametrize(
    "record",
    [
        '{"id":"x","input":"q","target":"a","target":"b"}',
        '{"id":"x","input":"q","target":NaN}',
        '{"decision":{"status":"pass"},"attempts":[]}',
    ],
)
def test_cli_import_rejects_ambiguous_json_and_results(tmp_path, record):
    source, output = tmp_path / "samples.jsonl", tmp_path / "taskset.json"
    source.write_text(record, encoding="utf-8")
    assert (
        main(
            [
                "import-inspect",
                str(source),
                "--taskset-id",
                "x",
                "--revision",
                "1",
                "--output",
                str(output),
            ]
        )
        == 64
    )
    assert not output.exists()


def test_cli_export_never_overwrites_existing_file(tmp_path):
    source, output = tmp_path / "tasks.json", tmp_path / "existing.jsonl"
    source.write_text(json.dumps(taskset()), encoding="utf-8")
    output.write_text("keep", encoding="utf-8")
    assert (
        main(
            [
                "export-dataset",
                str(source),
                "--format",
                "inspect",
                "--output",
                str(output),
            ]
        )
        == 64
    )
    assert output.read_text() == "keep"


def test_real_inspect_sdk_can_read_and_evaluate_export(tmp_path):
    pytest.importorskip("inspect_ai")
    # This is Inspect's evaluation API, never Python's code-executing builtin.
    from inspect_ai import eval as inspect_eval
    from inspect_ai.dataset import json_dataset
    from inspect_ai.model import ModelOutput

    from agentops.benchmarks.interop import inspect_task

    source = taskset()
    observed_budgets = []

    def mock_output(messages, tools, tool_choice, config):
        observed_budgets.append(config.max_tokens)
        return ModelOutput.from_content("mockllm/model", "42")

    path = tmp_path / "samples.jsonl"
    path.write_text(
        "\n".join(json.dumps(r) for r in export_dataset(source, "inspect")),
        encoding="utf-8",
    )
    samples = json_dataset(str(path))
    assert samples[0].target == "42"
    logs = inspect_eval(
        inspect_task(source),
        model="mockllm/model",
        model_args={"custom_outputs": mock_output},
        log_dir=str(tmp_path / "logs"),
        display="none",
    )
    assert logs[0].status == "success"
    assert logs[0].samples[0].scores["includes"].value == "C"
    assert observed_budgets == [32]
    # Native case-sensitive substring semantics must survive SDK integration.
    source["tasks"][0]["expected"] = "ANSWER"
    logs = inspect_eval(
        inspect_task(source),
        model="mockllm/model",
        model_args={
            "custom_outputs": [ModelOutput.from_content("mockllm/model", "answer")]
        },
        log_dir=str(tmp_path / "case"),
        display="none",
    )
    assert logs[0].samples[0].scores["includes"].value == "I"
