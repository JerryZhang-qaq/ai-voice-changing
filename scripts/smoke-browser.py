"""Real UI/API/worker check. Requires Playwright and a running workbench."""
import argparse
import json
from pathlib import Path
import shutil
import time

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("runtime/browser-check"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=shutil.which("chromium") or None, headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width":1440,"height":1000})
        errors, failures = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("response", lambda response: failures.append({"url":response.url,"status":response.status}) if response.status >= 500 else None)
        page.goto(args.url)
        page.get_by_role("heading",name="素材与数据集").wait_for()
        # Upload a uniquely named real audio file through the UI.
        name = "browser-check-" + str(time.time_ns()) + ".wav"
        page.locator('input[type="file"]').first.set_input_files({"name":name,"mimeType":"audio/wav","buffer":args.audio.read_bytes()})
        page.get_by_role("button",name="上传素材",exact=True).click()
        page.get_by_role("checkbox",name="选择素材 "+name).wait_for()
        page.get_by_role("checkbox",name="选择素材 "+name).check()
        page.get_by_label("处理模式").select_option("review")
        page.get_by_role("checkbox",name="筛查复杂和声（自动模式必选）").uncheck()
        page.get_by_role("button",name="准备所选素材",exact=True).click()
        def last_job(kind):
            return next(j for j in page.request.get(args.url+"/api/jobs").json() if j["kind"]==kind)
        def completed(kind):
            job = last_job(kind)
            deadline = time.monotonic()+60
            while job["status"] in {"queued","running"} and time.monotonic()<deadline:
                page.wait_for_timeout(300)
                job = page.request.get(args.url+"/api/jobs/"+job["id"]).json()
            assert job["status"]=="completed", job
            return job["metadata"]["result_id"]
        page.get_by_role("status").filter(has_text="已提交").wait_for()
        manifest_id = completed("dataset_prepare")
        page.get_by_role("button",name="刷新",exact=True).click()
        page.get_by_label("选择数据集").select_option(manifest_id)
        page.get_by_role("button",name="波形与边界编辑",exact=True).first.click()
        page.get_by_role("img",name="音频波形").first.wait_for()
        manifest = page.request.get(args.url+f"/api/datasets/{manifest_id}").json()
        clip = manifest["clips"][0]
        start = clip["valid_start_sample"]/clip["sample_rate"]+.1
        end = clip["valid_end_sample"]/clip["sample_rate"]-.1
        page.get_by_label("核心起点",exact=True).first.fill(str(start))
        page.get_by_label("核心终点",exact=True).first.fill(str(end))
        page.get_by_role("button",name="另存边界版本",exact=True).first.click()
        page.get_by_role("status").filter(has_text="边界编辑任务").wait_for()
        edited_id = completed("dataset_edit")
        page.get_by_role("button",name="刷新",exact=True).click()
        page.get_by_label("选择数据集").select_option(edited_id)
        page.get_by_label("片段 1 审查决定",exact=True).select_option("accepted")
        page.get_by_role("button",name="保存审查为新版本",exact=True).click()
        page.get_by_role("status").filter(has_text="已保存新的数据集版本").wait_for()
        reviewed_id = page.get_by_label("选择数据集").input_value()
        reviewed = page.request.get(args.url+f"/api/datasets/{reviewed_id}").json()
        assert reviewed["summary"]["accepted_count"]==1
        page.get_by_role("button",name="导出已接受数据集",exact=True).click()
        page.get_by_role("status").filter(has_text="数据集导出任务").wait_for()
        export_id = completed("dataset_export")
        assert page.request.get(args.url+f"/api/artifacts/{export_id}/file").status==200
        page.screenshot(path=str(args.output/"dataset.png"),full_page=True)
        for nav in ["人声与和声分离","RVC 模型与翻唱","混音与导出","引擎与基础模型","任务中心","缓存与存储"]:
            page.get_by_role("navigation").get_by_role("button",name=nav,exact=True).click()
            page.get_by_role("heading",name=nav,exact=True).wait_for()
        source_id = next(a["id"] for a in page.request.get(args.url+"/api/artifacts").json()["items"] if a["name"]==name)
        accepted_id = next(c["artifact_id"] for c in reviewed["clips"] if c["status"]=="accepted")
        # The cache API is also exercised by existing UI-specific tests; here
        # verify the active browser workflow's protected audio survives it.
        cleaned = page.request.post(args.url+"/api/cache/cleanup",data={}).json()
        assert accepted_id not in cleaned["deleted_ids"] and source_id not in cleaned["deleted_ids"]
        assert page.request.get(args.url+f"/api/artifacts/{accepted_id}/file").status==200
        assert not errors and not failures, {"errors":errors,"failures":failures}
        (args.output/"report.json").write_text(json.dumps({"status":"passed","pages":7,"dataset_id":reviewed_id,"export_id":export_id,"console_errors":errors,"server_errors":failures},indent=2))
        browser.close()
        print("七个页面、上传、切片、边界编辑、人工接受、导出与缓存保护通过。")


if __name__ == "__main__":
    main()
