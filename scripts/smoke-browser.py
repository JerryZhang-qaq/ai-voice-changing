"""0.0.3 browser workflow check; fixture runs explicitly identify GPU substitutes."""
import argparse
import json
from pathlib import Path
import shutil
import time
from playwright.sync_api import sync_playwright


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:8000')
    parser.add_argument('--audio',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=Path('runtime/browser-check'))
    parser.add_argument('--fixture',action='store_true',help='Explicit CPU fixture; no GPU or download quality claim')
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    folder=args.output/'Aimer'
    folder.mkdir(exist_ok=True)
    shutil.copyfile(args.audio,folder/'singing.wav')
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path=shutil.which('chromium') or None,headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':1000})
        errors,failures=[],[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.on('response',lambda response:failures.append({'url':response.url,'status':response.status}) if response.status>=500 else None)
        page.goto(args.url)
        page.get_by_role('heading',name='训练素材与数据集',exact=True).wait_for()
        page.get_by_label('训练音源文件夹',exact=True).set_input_files(str(folder))
        assert page.get_by_label('训练歌手名字',exact=True).input_value()=='Aimer'
        page.get_by_role('combobox',name='文件夹内音源类型').select_option('dry_vocal')
        page.get_by_role('button',name='导入整个文件夹',exact=True).click()
        page.get_by_role('status').filter(has_text='已导入 1 个音源').wait_for()
        assert page.get_by_label('训练素材文件夹',exact=True).input_value()=='Aimer'
        page.get_by_role('checkbox',name='我确认文件夹内均为该歌手的独唱素材').check()
        page.get_by_role('button',name='批量自动处理整个文件夹',exact=True).click()
        page.get_by_role('progressbar',name='批量准备训练数据进度').wait_for()

        def complete(kind):
            deadline=time.monotonic()+90
            while time.monotonic()<deadline:
                jobs=page.request.get(args.url+'/api/jobs').json()
                job=next((j for j in jobs if j['kind']==kind),None)
                if job and job['status'] not in {'queued','running'}:
                    assert job['status']=='completed',job
                    return job
                page.wait_for_timeout(300)
            raise AssertionError(f'Timeout: {kind}')
        prepared=complete('dataset_prepare')['metadata']['result_id']
        page.get_by_label('选择数据集',exact=True).select_option(prepared)
        page.get_by_role('group',name='片段 1 审查决定',exact=True).wait_for()
        page.get_by_role('button',name='下载处理后干声合集',exact=True).click()
        export=complete('dataset_export')['metadata']['result_id']
        assert page.request.get(args.url+f'/api/artifacts/{export}/file').status==200
        page.get_by_role('group',name='片段 1 审查决定',exact=True).get_by_role('button',name='Y · 接受',exact=True).click()
        group=page.get_by_role('group',name='片段 2 审查决定',exact=True)
        if group.count(): group.get_by_role('button',name='Y · 接受',exact=True).click()
        page.get_by_role('button',name='保存审查为新版本',exact=True).click()
        page.get_by_role('status').filter(has_text='已保存新的数据集版本').wait_for()
        reviewed=page.get_by_label('选择数据集',exact=True).input_value()
        manifest=page.request.get(args.url+f'/api/datasets/{reviewed}').json()
        assert manifest['summary']['accepted_count']>=1
        page.get_by_role('button',name='导出已接受数据集',exact=True).click()
        export=complete('dataset_export')['metadata']['result_id']
        # Wait for the newest export instead of returning a prior completed job.
        page.wait_for_function("()=>document.querySelectorAll('.task-completed').length>=2")
        page.get_by_role('button',name='波形与边界编辑',exact=True).first.click()
        page.get_by_role('img',name='音频波形',exact=True).first.wait_for()
        top=page.locator('aside').bounding_box()['y']
        page.locator('main').evaluate('(node)=>node.scrollTop=500')
        assert page.locator('main').evaluate('(node)=>node.scrollTop')>0
        assert page.locator('aside').bounding_box()['y']==top
        assert page.evaluate('window.scrollY')==0
        page.screenshot(path=str(args.output/'dataset.png'),full_page=True)

        def nav(name):
            page.get_by_role('navigation').get_by_role('button',name=name,exact=True).click()
            page.get_by_role('heading',name=name,exact=True).wait_for()
        nav('训练与音色模型')
        if args.fixture and manifest['summary']['accepted_count']>=2:
            page.get_by_label('训练数据集',exact=True).select_option(reviewed)
            page.get_by_role('button',name='开始训练',exact=True).click()
            page.get_by_role('progressbar',name='RVC 训练进度',exact=True).wait_for()
            page.get_by_role('img',name='训练损失曲线',exact=True).wait_for()
            page.screenshot(path=str(args.output/'training.png'),full_page=True)
            complete('rvc_train')
        nav('转换素材与翻唱')
        options=page.get_by_label('转换输入音频',exact=True).locator('option').all_text_contents()
        assert not any('singing.wav' in option for option in options)
        page.get_by_label('转换素材歌手',exact=True).fill('原唱')
        page.get_by_label('转换音源文件',exact=True).set_input_files(str(args.audio))
        page.get_by_role('button',name='导入转换音源',exact=True).click()
        page.get_by_role('status').filter(has_text='转换素材已导入').wait_for()
        page.get_by_role('button',name='一键分离伴奏和和声',exact=True).click()
        page.get_by_role('progressbar',name='人声、伴奏与和声分离进度',exact=True).wait_for()
        complete('song_separation')
        page.get_by_role('link',name='下载伴奏',exact=True).last.wait_for()
        with page.expect_download() as download:
            page.get_by_role('link',name='下载伴奏',exact=True).last.click()
        assert 'instrumental' in download.value.suggested_filename
        page.get_by_role('link',name='下载和声',exact=True).last.wait_for()
        page.screenshot(path=str(args.output/'conversion.png'),full_page=True)
        nav('引擎与基础模型')
        if args.fixture:
            page.get_by_role('button',name='下载 / 校验所选资源',exact=True).click()
            dialog=page.get_by_role('dialog',name='资源下载与校验',exact=True)
            dialog.wait_for()
            dialog.get_by_role('progressbar',name='资源下载与校验进度',exact=True).wait_for()
            dialog.get_by_text('下载已开始，请保持工作台运行。',exact=True).wait_for()
            page.screenshot(path=str(args.output/'download.png'),full_page=True)
            dialog.get_by_role('button',name='关闭弹窗，继续后台任务',exact=True).click()
            complete('resources')
        for name in ['人声与和声分离','混音与导出','任务中心','缓存与存储']:nav(name)
        page.get_by_role('button',name='清理全部缓存（含手动保留）',exact=True).click()
        page.get_by_role('dialog',name='确认手动清理缓存',exact=True).wait_for()
        page.get_by_role('button',name='确认清理',exact=True).click()
        complete('storage_cleanup')
        nav('训练素材与数据集')
        accepted=[c['artifact_id'] for c in manifest['clips'] if c['status']=='accepted']
        assert all(page.request.get(args.url+f'/api/artifacts/{aid}/file').status==200 for aid in accepted)
        assert not errors and not failures,{'errors':errors,'failures':failures}
        report={'status':'passed','version':'0.0.3','pages':8,'fixture_gpu_cores':args.fixture,'checks':['folder import','singer inference','batch prepare inline progress','Y/N review','readable after/ready ZIP','waveform','fixed sidebar','training/conversion isolation','stem downloads','download modal','manual cache cleanup','accepted clip protection'],'console_errors':errors,'server_errors':failures}
        (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        browser.close()
        print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()
