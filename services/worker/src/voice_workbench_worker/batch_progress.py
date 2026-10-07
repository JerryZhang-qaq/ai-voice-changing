"""Persist batch work independently from the current model's chunk counter."""
import time


class BatchProgress:
    def __init__(self, store, job_id, phases, sources):
        self.store, self.job_id, self.phases, self.sources = store, job_id, phases, sources
        self.completed = 0
        self.timings = {}
        self.current = None
        self.total = sum(len(indices) for _, _, indices in phases)
        store.update_job(job_id, "running", metadata={"batch_progress": {"done": 0, "total": self.total,
                         "completed_steps": 0, "phases": [label for _, label, _ in phases], "source_total": len(sources)}})

    def begin(self, phase, index):
        self.current = (phase, index, time.monotonic())
        phase_index = next(i for i, (key, _, _) in enumerate(self.phases) if key == phase)
        _, label, indices = self.phases[phase_index]
        position = indices.index(index)
        state = self.store.job(self.job_id)["metadata"]["batch_progress"]
        durations = sum(self.sources[i].get("metadata", {}).get("audio", {}).get("duration", 0) for i in indices[position:])
        seconds, audio_seconds = self.timings.get(phase, (0, 0))
        self.store.update_job(self.job_id, "running", metadata={"engine_progress": None,
            "batch_progress": {**state, "done": max(state.get("done", 0), self.completed), "completed_steps": self.completed,
                "phase": phase, "phase_index": phase_index, "phase_label": label, "phase_done": position, "phase_total": len(indices),
                "source_index": index + 1, "source_name": self.sources[index]["name"], "clip_index": None, "clip_total": None,
                "phase_eta_seconds": seconds * durations / audio_seconds if audio_seconds > 0 else None}})

    def clip(self, index, total):
        state = self.store.job(self.job_id)["metadata"]["batch_progress"]
        self.store.update_job(self.job_id, "running", metadata={"batch_progress": {**state, "clip_index": index + 1, "clip_total": total},
                                                               "engine_progress": None})

    def finish(self):
        phase, index, started = self.current
        elapsed = time.monotonic() - started
        metadata = self.store.job(self.job_id)["metadata"]
        duration = self.sources[index].get("metadata", {}).get("audio", {}).get("duration", 0)
        if duration > 0 and not (metadata.get("engine_progress") or {}).get("cache_hit"):
            seconds, audio_seconds = self.timings.get(phase, (0, 0))
            self.timings[phase] = (seconds + elapsed, audio_seconds + duration)
        self.completed += 1
        state = metadata["batch_progress"]
        self.store.update_job(self.job_id, "running", metadata={"batch_progress": {**state, "completed_steps": self.completed,
                        "done": self.completed, "phase_done": state["phase_done"] + 1}, "engine_progress": None})

    def complete(self):
        state = self.store.job(self.job_id)["metadata"]["batch_progress"]
        self.store.update_job(self.job_id, "running", metadata={"batch_progress": {**state, "done": self.total, "completed_steps": self.total,
                         "phase_done": state.get("phase_total", 0), "phase_eta_seconds": 0}, "engine_progress": None})
