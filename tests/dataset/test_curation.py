from copy import deepcopy

from voice_workbench_dataset.curation import assign_split


def manifest(groups):
    return {"clips": [{"artifact_id": str(i), "source_group": group, "status": "accepted"} for i,group in enumerate(groups)], "summary": {}}


def test_same_source_never_spans_training_and_validation():
    data = manifest(["a","a","b","b","c","c"])
    result = assign_split(data)
    train, validation = set(result["split"]["train"]), set(result["split"]["validation"])
    assert train and validation and train.isdisjoint(validation)
    for group in {c["source_group"] for c in data["clips"]}:
        ids = {c["artifact_id"] for c in data["clips"] if c["source_group"]==group}
        assert ids <= train or ids <= validation
    assert assign_split(deepcopy(data))["split"] == result["split"]


def test_single_source_does_not_claim_independent_validation():
    result = assign_split(manifest(["a","a"]))
    assert result["split"]["validation"] == []
    assert result["validation"]["status"] == "unavailable_single_source"


def test_duplicate_links_merge_sources_even_when_bridge_clip_excluded():
    data = manifest(["a", "b", "c", "d"])
    data["clips"][1].update(status="excluded", near_duplicate_of=["0"])
    data["clips"][2].update(near_duplicate_of=["1"])
    data["clips"].append({"artifact_id": "4", "source_group": "b", "status": "accepted"})
    result = assign_split(data)
    train, validation = set(result["split"]["train"]), set(result["split"]["validation"])
    linked = {"0", "2", "4"}
    assert linked <= train or linked <= validation
    assert result["split"]["linked_source_count"] == 2
    assert result["validation"]["groups"] == 2


def test_exact_duplicate_sources_cannot_claim_independent_validation():
    data = manifest(["a", "b"])
    for clip in data["clips"]:
        clip["duplicate_group"] = "same-recording"
    result = assign_split(data)
    assert result["validation"]["status"] == "unavailable_single_source"
    assert result["split"]["validation"] == []
