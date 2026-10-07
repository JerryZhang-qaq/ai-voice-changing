"""Instrument the pinned RVC batch loop without editing its checkout.

Only train.train is intercepted. All original training operations are preserved;
rank 0 emits observed batch/loss values, at most once per second plus epoch end.
"""
import ast
import importlib.abc
import importlib.machinery
import os
import sys


HELPER = '''
def _workbench_batch(values):
    import json, time
    if values.get("rank", 0) != 0:
        return
    now = time.monotonic()
    batch, batches = values["batch_idx"] + 1, len(values["train_loader"])
    if batch != batches and now - globals().get("_workbench_last_emit", 0) < 1:
        return
    globals()["_workbench_last_emit"] = now
    hps = values["hps"]
    print(json.dumps({"event": "workbench_training", "epoch": values["epoch"],
        "epochs": hps.total_epoch, "batch": batch, "batches": batches, "step": globals()["global_step"],
        "loss_g": float(values["loss_gen_all"].detach().item()),
        "loss_d": float(values["loss_disc"].detach().item())}), flush=True)
'''


class Instrument(ast.NodeTransformer):
    count = 0

    def visit_AugAssign(self, node):
        if isinstance(node.target, ast.Name) and node.target.id == "global_step" and isinstance(node.op, ast.Add):
            self.count += 1
            return [node, ast.parse("_workbench_batch(locals())").body[0]]
        return node


def instrument(source, filename):
    tree = ast.parse(source, filename)
    transform = Instrument()
    tree = transform.visit(tree)
    if transform.count != 1:
        raise RuntimeError("RVC 训练循环与固定版本不符，无法安全添加进度观测")
    position = 0
    for index, node in enumerate(tree.body):
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            position = index + 1
    tree.body[position:position] = ast.parse(HELPER).body
    return compile(ast.fix_missing_locations(tree), filename, "exec")


class Loader(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):
        return instrument(self.get_data(self.path).decode("utf-8-sig"), self.path)


class Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname != "train.train":
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec and spec.origin:
            spec.loader = Loader(fullname, spec.origin)
        return spec


if os.environ.get("WORKBENCH_TRAIN_TELEMETRY") == "1":
    sys.meta_path.insert(0, Finder())
