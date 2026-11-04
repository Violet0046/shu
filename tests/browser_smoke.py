"""手动运行的浏览器验收，使用模拟模型，不读取真实模型密钥。

python tests/browser_smoke.py <截图目录>
需要开发环境安装 playwright；Windows 使用已安装的 Edge。
"""
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

from test_web_workflow import FakeProvider
from shu_grading.web.server import create_app


def main(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="shu-browser-") as tmp:
        root = Path(tmp)
        image = root / 'essay.png'
        paper = Image.new('RGB', (800, 1100), '#fffdf6')
        draw = ImageDraw.Draw(paper)
        font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 27)
        draw.text((68, 64), '那一刻，我长大了', font=font, fill='#26354a')
        draw.text((68, 120), '浏览器验收用示例 · 非真实学生答卷', font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 20), fill='#8a8d92')
        sample = ['成长，有时候就在一个平凡的瞬间。','那天放学，窗外的雨一直没有停。','我站在校门口，想起早上与母亲的争执，','心里有些懊恼，却又不知道如何开口。','远处，一个熟悉的身影撑着伞走来。','她的肩膀已经湿了，却把伞向我这边倾斜。','我接过她手里的袋子，轻轻说了声谢谢。','那一刻，我开始懂得，成长意味着体谅。']
        for i in range(17):
            y = 200 + i*47
            draw.line((65, y+38, 735, y+38), fill='#e2dacc', width=1)
            if i < len(sample):
                draw.text((68, y), sample[i], font=font, fill='#434d5c')
        paper.save(image)
        app = create_app(root / 'data', provider_factory=lambda: FakeProvider(),start_worker=True)
        app.state.services.auth.create_account('验收老师','browser-test-password')
        app.state.services.auth.set_admin('验收老师',True)
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level='warning'))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(.05)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless=True)
                page = browser.new_page(viewport={'width': 1520, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{port}')
                page.locator('[name=username]').fill('验收老师')
                page.locator('[name=password]').fill('browser-test-password')
                page.get_by_role('button', name='登录',exact=True).click()
                page.get_by_role('button', name='创建阅卷任务', exact=True).wait_for()
                page.get_by_role('button',name='账户菜单').hover()
                page.locator('.admin-account-link').click()
                page.get_by_text('今日模型调用',exact=True).wait_for()
                assert '正常' in page.locator('.grading-service-status').inner_text()
                assert page.get_by_role('heading',name='工作进程',exact=True).count() == 0
                page.screenshot(path=str(output/'web-admin-overview.png'),full_page=True)
                page.get_by_role('button',name='账户管理',exact=True).click()
                page.get_by_role('button',name='创建教师账户',exact=True).click()
                page.locator('#admin-create-form [name=username]').fill('浏览器测试老师')
                page.locator('#admin-create-form [name=password]').fill('created-test-password')
                page.locator('#admin-create-form [name=recipient]').fill('后台创建验收')
                page.locator('#admin-create-form').get_by_role('button',name='创建账户',exact=True).click()
                page.locator('#notice-dialog').get_by_role('button',name='知道了').click()
                account=page.locator('.admin-table tr').filter(has_text='浏览器测试老师').first
                account.get_by_role('button',name='重置密码',exact=True).click()
                page.locator('#admin-reset-form [name=password]').fill('reset-test-password')
                page.locator('#admin-reset-form').get_by_role('button',name='确认重置',exact=True).click()
                page.locator('#notice-dialog').get_by_role('button',name='知道了').click()
                account=page.locator('.admin-table tr').filter(has_text='浏览器测试老师').first
                account.get_by_role('button',name='停用',exact=True).click()
                page.locator('#notice-dialog').get_by_role('button',name='确认',exact=True).click()
                page.locator('#notice-dialog').get_by_role('button',name='知道了').click()
                page.locator('.admin-table tr').filter(has_text='浏览器测试老师').first.get_by_role('button',name='启用',exact=True).wait_for()
                page.screenshot(path=str(output/'web-admin-accounts.png'),full_page=True)
                page.get_by_role('button',name='返回阅卷工作台',exact=True).click()
                page.screenshot(path=str(output/'web-v3-home.png'), full_page=True)
                page.get_by_role('button', name='创建阅卷任务', exact=True).click()
                assert page.locator('#template-select option:checked').text_content() == '高中记叙文·50分制'
                assert page.locator('[data-rule=description]').first.input_value()
                page.locator('#task-title').fill('高一记叙文 · 逐份阅卷验收')
                page.locator('#assignment').fill('以“那一刻，我长大了”为题，写一篇记叙文。')
                # 模拟模型返回内容、语言两个维度；通过真实表单编辑。
                page.locator('[data-action=remove-rule]').first.click()
                page.locator('[data-action=remove-rule]').first.click()
                page.locator('[data-action=remove-rule]').first.click()
                page.locator('[data-rule=name]').nth(0).fill('内容')
                page.locator('[data-rule=points]').nth(0).fill('30')
                page.locator('[data-rule=description]').nth(0).fill('内容具体充实，感情真实。')
                page.locator('[data-rule=points]').nth(1).fill('20')
                page.locator('[data-rule=name]').nth(1).fill('语言')
                page.locator('[data-rule=description]').nth(1).fill('表达通顺，用词准确。')
                page.locator('#save-template').check()
                page.locator('#save-template-name').fill('我的记叙文标准')
                page.screenshot(path=str(output/'web-v3-task-form.png'), full_page=True)
                # 阅卷服务不可用时应明确提示，恢复后提示消失。
                page.route('**/api/health',lambda route:route.fulfill(json={'database':'ok','worker_available':False,'embedded_worker':False}))
                page.get_by_role('button', name='创建任务，开始录入').click()
                page.locator('#worker-notice').wait_for(state='visible')
                assert '请勿重复上传' in page.locator('#worker-notice').inner_text()
                page.unroute('**/api/health')
                page.evaluate('checkWorkerHealth(true)')
                assert not page.locator('#worker-notice').is_visible()
                page.locator('.task-title-wrap h1').hover()
                assert page.locator('#assignment-popover').is_visible()
                assert '那一刻，我长大了' in page.locator('#assignment-popover').inner_text()
                page.screenshot(path=str(output/'web-task-requirements.png'), full_page=True)
                page.get_by_role('button',name='下载已确认成绩').click()
                page.locator('#notice-dialog').get_by_text('还没有可导出的已确认成绩').wait_for()
                assert '/api/tasks/' not in page.url
                page.screenshot(path=str(output/'web-download-notice.png'), full_page=True)
                page.locator('#notice-dialog').get_by_role('button',name='知道了').click()
                page.locator('#image-file').set_input_files(str(image))
                page.get_by_role('button', name='提交并录入下一位').click()
                page.locator('.directory-item .tag.review').wait_for(timeout=15000)
                page.locator('.directory-item[data-action=directory-student]').first.click()
                page.locator('#review-form').wait_for(timeout=15000)
                assert page.locator('.uncertain-char').inner_text() == '［疑字］'
                assert page.locator('.directory-item .tag.review').count() == 1
                assert page.locator('.sticky-actions button').all_text_contents() == ['确认完成','AI 重审']
                assert '含标点，不含标题' in page.locator('.paper-toolbar .word-count').inner_text()
                assert page.locator('.paper-hint').inner_text() == '转写不润色、不自动纠错；［疑字］请核对原图'
                assert page.get_by_role('button', name='阅卷首页').count() == 0
                page.get_by_role('button', name='账户菜单').hover()
                assert page.locator('.account-dropdown').is_visible()
                assert '验收老师' in page.locator('.account-profile').inner_text()
                assert page.locator('.account-profile').inner_text() == '验收老师'
                assert page.locator('.admin-account-link').evaluate('(el)=>getComputedStyle(el).color') == 'rgb(251, 114, 153)'
                assert page.locator('.account-avatar').get_attribute('title') is None
                page.screenshot(path=str(output/'web-account-profile.png'), full_page=True)
                assert page.locator('.account-dropdown > button').all_text_contents() == ['我的班级','评分标准','退出登录']
                page.locator('h1').hover()
                page.screenshot(path=str(output/'web-paper-toolbar.png'), full_page=True)
                page.get_by_text('我接过她手里的［疑字］', exact=False).wait_for()
                page.get_by_role('button', name='AI 重审', exact=True).click()
                assert page.locator('.notice-message').inner_text() == 'AI 重审将再次调用模型，继续吗？'
                page.locator('#notice-dialog').get_by_role('button',name='确认',exact=True).click()
                page.wait_for_function("state.task.submissions[0].revision >= 3 && state.task.submissions[0].status === 'ready'")
                page.locator('#presentation-deduction').fill('2')
                page.locator('[data-score="0"]').fill('25')
                page.locator('#review-comment').fill('老师修改的评语：选材具体，请继续补充细节。')
                page.get_by_role('button', name='确认完成').click()
                page.wait_for_function("document.querySelector('#task-count').textContent.includes('已确认 1 份')")
                page.locator('#toast').wait_for(state='hidden', timeout=7000)
                assert page.locator('#teacher-total').text_content() == '39'
                page.get_by_role('button',name='原图',exact=True).click()
                page.locator('#original-image').wait_for()
                assert 'preview=true' in page.locator('#original-image').get_attribute('src')
                assert page.locator('.paper-hint').inner_text() == '点击图片后，可按住 Ctrl 用滚轮缩放'
                page.screenshot(path=str(output/'web-v3-review.png'), full_page=True)
                # Delay the reading image: preview must remain visible and zoom must survive upgrade.
                page.wait_for_function("document.querySelector('#original-image').naturalWidth>0")
                page.evaluate('clearImageCache()')
                held=[]
                page.route('**/image?reading=true',lambda route:held.append(route))
                page.locator('#original-image').click()
                page.wait_for_function("imageViewer?.width>0 && imageViewer.quality==='preview'")
                page.evaluate('zoomImage(1.1,400,300)')
                geometry=page.evaluate('({x:imageViewer.x,y:imageViewer.y,width:imageViewer.width*imageViewer.scale})')
                page.wait_for_timeout(100)
                assert held
                import io
                high=io.BytesIO()
                paper.resize((1600,2200)).save(high,'JPEG')
                held[0].fulfill(status=200,content_type='image/jpeg',body=high.getvalue())
                page.wait_for_function("imageViewer?.quality==='reading'")
                upgraded=page.evaluate('({x:imageViewer.x,y:imageViewer.y,width:imageViewer.width*imageViewer.scale})')
                assert all(abs(geometry[k]-upgraded[k])<1 for k in geometry)
                page.get_by_role('button',name='关闭原图',exact=True).click()
                page.unroute('**/image?reading=true')
                page.evaluate('clearImageCache()')
                page.locator('#original-image').click()
                page.locator('#viewer-image').wait_for()
                page.wait_for_function('imageViewer && imageViewer.width > 0')
                assert page.locator('#image-viewer').inner_text()==''
                page.screenshot(path=str(output/'web-v4-image-viewer.png'),full_page=False)
                bounds=page.locator('#viewer-stage').bounding_box()
                px,py=bounds['x']+bounds['width']*.42,bounds['y']+bounds['height']*.35
                before=page.evaluate('({x:imageViewer.x,y:imageViewer.y,scale:imageViewer.scale})')
                page_metrics=page.evaluate('({width:innerWidth,dpr:devicePixelRatio})')
                modal_bounds=page.locator('#image-viewer').bounding_box()
                assert modal_bounds['x']==0 and modal_bounds['y']==0
                assert modal_bounds['height']==1000
                page.mouse.move(px,py)
                page.evaluate("document.querySelector('#viewer-stage').addEventListener('wheel',e=>window.testWheel={x:e.clientX,y:e.clientY},{once:true})")
                page.keyboard.down('Control')
                page.mouse.wheel(0,-400)
                page.keyboard.up('Control')
                page.wait_for_function('(s)=>imageViewer.scale>s',arg=before['scale'])
                after=page.evaluate('({x:imageViewer.x,y:imageViewer.y,scale:imageViewer.scale})')
                delivered=page.evaluate('window.testWheel')
                px,py=delivered['x'],delivered['y']
                assert page.evaluate('window.visualViewport.scale')==1
                assert page.evaluate('({width:innerWidth,dpr:devicePixelRatio})')==page_metrics
                assert abs(((px-bounds['x'])-before['x'])/before['scale']-((px-bounds['x'])-after['x'])/after['scale'])<.01
                assert abs(((py-bounds['y'])-before['y'])/before['scale']-((py-bounds['y'])-after['y'])/after['scale'])<.01
                page.mouse.down()
                page.mouse.move(px+90,py+40,steps=5)
                page.mouse.up()
                moved=page.evaluate('({x:imageViewer.x,y:imageViewer.y})')
                assert moved['x']==after['x'] and moved['y']==after['y']
                page.mouse.wheel(40,200)
                page.wait_for_timeout(100)
                assert page.evaluate('({x:imageViewer.x,y:imageViewer.y,scale:imageViewer.scale})')==after
                page.keyboard.press('Escape')
                assert not page.locator('#image-viewer').is_visible()
                assert not page.locator('body').evaluate("e=>e.classList.contains('viewing-image')")
                page.locator('#original-image').click()
                page.wait_for_function('imageViewer && imageViewer.width > 0')
                page.locator('#viewer-image').click()
                assert page.locator('#image-viewer').is_visible()
                page.locator('#viewer-stage').click(position={'x':10,'y':500})
                assert not page.locator('#image-viewer').is_visible()
                page.locator('#original-image').click()
                page.get_by_role('button',name='关闭原图',exact=True).click()
                assert not page.locator('#image-viewer').is_visible()
                with page.expect_download() as download:
                    page.get_by_role('button', name='下载已确认成绩').click()
                assert download.value.suggested_filename == '高一记叙文 · 逐份阅卷验收.xlsx'
                page.on('dialog', lambda dialog: dialog.accept())
                assert page.get_by_role('button', name='AI 重审', exact=True).count() == 0
                page.get_by_role('button',name='账户菜单').hover()
                page.get_by_role('button', name='评分标准', exact=True).click()
                page.locator('.task-row').filter(has_text='我的记叙文标准').get_by_role('button', name='编辑模板').click()
                page.locator('#template-name').fill('更新后的标准')
                page.get_by_role('button', name='更新模板').click()
                page.get_by_text('更新后的标准', exact=True).wait_for()
                page.locator('.task-row').filter(has_text='更新后的标准').get_by_role('button',name='设为默认').click()
                page.locator('.task-row').filter(has_text='高中记叙文·50分制').get_by_role('button',name='删除',exact=True).click()
                page.locator('#notice-dialog').get_by_role('button',name='确认',exact=True).click()
                page.wait_for_function("!document.querySelector('#app').textContent.includes('高中记叙文·50分制')")
                page.get_by_role('button', name='我的班级', exact=True).click()
                page.get_by_role('button', name='新建班级').click()
                page.locator('#class-name').fill('高一（3）班')
                page.locator('[data-student-name]').first.fill('张同学')
                page.get_by_role('button', name='＋ 添加学生').click()
                page.locator('[data-student-name]').nth(1).fill('李同学')
                page.get_by_role('button', name='保存班级').click()
                page.get_by_text('高一（3）班', exact=True).wait_for()
                page.get_by_role('button',name='编辑班级名单').click()
                page.locator('[data-student-name]').nth(1).fill('李同学改名')
                page.get_by_role('button',name='保存班级').click()
                page.locator('#dialog').wait_for(state='hidden')
                # 实名任务：目录搜索、状态、连续录入、完成录入与复核分开。
                page.locator('.brand').click()
                page.get_by_role('button',name='创建阅卷任务',exact=True).click()
                assert page.locator('#template-select option:checked').text_content() == '更新后的标准'
                page.locator('#task-title').fill('班级测验 · 连续录入')
                page.locator('#task-mode').select_option('named')
                page.locator('#task-class').select_option(label='高一（3）班')
                page.locator('#assignment').fill('那一刻，我长大了')
                page.get_by_role('button',name='创建任务，开始录入').click()
                page.locator('#directory-search').fill('张')
                assert page.locator('.directory-item').count() == 1
                page.locator('#directory-search').fill('')
                page.locator('#toast').wait_for(state='hidden',timeout=7000)
                page.screenshot(path=str(output/'web-v3-entry.png'), full_page=True)
                page.locator('#directory-search').fill('张')
                # Reproduce a polling snapshot taken before POST finishes.
                page.evaluate('''() => {const original=window.fetch;let hold=true;window.restoreFetch=()=>window.fetch=original;
                    window.fetch=async(...args)=>{const r=await original(...args);const url=String(args[0]);
                    if(hold&&url==='/api/tasks/'+state.task.id&&(!args[1]?.method||args[1].method==='GET')){hold=false;await new Promise(resolve=>window.releasePoll=resolve);}
                    if(url.endsWith('/submissions')&&args[1]?.method==='POST')setTimeout(()=>window.releasePoll?.(),100);
                    return r;};void refreshTask();}''')
                page.wait_for_function('Boolean(window.releasePoll)')
                page.locator('#image-file').set_input_files(str(image))
                page.get_by_role('button',name='提交并录入下一位').click()
                page.get_by_text('当前学生：李同学改名',exact=True).wait_for()
                page.evaluate('window.restoreFetch()')
                assert page.locator('#directory-search').input_value()==''
                assert page.locator('.directory-item strong').all_text_contents()==['李同学改名','张同学']
                first=page.locator('.directory-item').first
                name_box=first.locator('strong').bounding_box()
                status_box=first.locator('.tag').bounding_box()
                assert abs((name_box['y']+name_box['height']/2)-(status_box['y']+status_box['height']/2))<2
                assert '001' not in page.locator('#directory-list').inner_text()
                page.locator('#directory-search').fill('001')
                assert page.locator('.directory-item').count()==0
                page.locator('#directory-search').fill('')
                page.locator('#image-file').set_input_files(str(image))
                page.get_by_role('button',name='提交并录入下一位').click()
                page.wait_for_function("document.querySelector('#task-count').textContent.includes('录入 2 / 2')")
                assert page.locator('[data-action=return-entry], [data-action=intake], [data-action=sync-class]').count()==0
                page.wait_for_function("state.task.submissions.every(s=>s.status==='ready')")
                page.locator('.directory-item[data-action=directory-student]').first.click()
                page.get_by_role('button',name='确认并下一位',exact=True).click()
                page.get_by_role('button',name='确认完成',exact=True).wait_for()
                page.get_by_role('button',name='确认完成',exact=True).click()
                page.wait_for_function("state.task.submissions.every(s=>s.confirmed)")
                assert not errors, errors
                # 手机入口由真实 API 签发；使用独立浏览器上下文验证无登录拍照。
                import os
                os.environ['SHU_PUBLIC_URL'] = f'http://127.0.0.1:{port}'
                tid = page.evaluate("async () => (await (await fetch('/api/tasks')).json()).find(t=>t.mode==='anonymous').id")
                pair = page.evaluate("async tid => (await (await fetch('/api/tasks/'+tid+'/pair', {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).json())", tid)
                mobile = browser.new_context(viewport={'width':390,'height':844}, is_mobile=True, has_touch=True)
                phone = mobile.new_page()
                phone.add_init_script("Object.defineProperty(crypto,'randomUUID',{value:undefined})")
                phone.goto(pair['url'])
                phone.get_by_text('拍摄一份作文',exact=True).wait_for()
                assert phone.locator('a.brand').count()==0
                capture_url=phone.url
                phone.locator('.brand').click()
                assert phone.url==capture_url
                assert phone.locator('#upload-form').is_visible()
                assert phone.get_by_text('拍照',exact=True).is_visible()
                phone.locator('#image-file').set_input_files(str(image))
                phone.get_by_role('button',name='提交并拍下一份').click()
                phone.get_by_text('答卷 002已提交，可继续录入下一份').wait_for()
                assert not phone.locator('#upload-preview').is_visible()
                phone.locator('#toast').wait_for(state='hidden', timeout=7000)
                phone.screenshot(path=str(output/'web-v3-mobile-capture.png'), full_page=True)
                assert phone.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                page.evaluate('openTask('+repr(tid)+')')
                page.get_by_role('button',name='手机扫码拍照',exact=True).click()
                page.locator('#dialog').wait_for(state='visible')
                page.locator('#dialog').wait_for(state='visible')
                assert page.get_by_label('手机拍照链接',exact=True).input_value()==pair['url']
                assert page.locator('#dialog-content .pair-footer').get_by_role('button',name='关闭拍照入口',exact=True).is_visible()
                page.wait_for_function("document.querySelector('#dialog-content img')?.naturalWidth > 0")
                page.screenshot(path=str(output/'web-capture-dialog.png'),full_page=True)
                page.get_by_role('button',name='关闭弹窗',exact=True).click()
                page.locator('#dialog').wait_for(state='hidden')
                page.get_by_role('button',name='手机扫码拍照',exact=True).click()
                page.locator('#dialog').wait_for(state='visible')
                assert page.get_by_label('手机拍照链接',exact=True).input_value()==pair['url']
                page.get_by_role('button',name='关闭拍照入口',exact=True).click()
                phone.evaluate('checkCapture()')
                phone.get_by_role('heading',name='拍照入口已失效',exact=True).wait_for()
                assert phone.locator('#upload-form').count()==0
                phone.reload()
                phone.get_by_role('heading',name='拍照入口不可用',exact=True).wait_for()
                assert phone.locator('a.brand').count()==0
                phone.locator('.brand').click()
                assert phone.locator('#auth-form').count()==0
                assert phone.locator('#upload-form').count()==0
                page.get_by_role('button',name='手机扫码拍照',exact=True).click()
                page.locator('#dialog').wait_for(state='visible')
                new_url=page.get_by_label('手机拍照链接',exact=True).input_value()
                assert new_url!=pair['url']
                page.get_by_role('button',name='关闭弹窗',exact=True).click()
                phone.evaluate('(url)=>location.hash=new URL(url).hash',new_url)
                phone.locator('#upload-form').wait_for()
                phone.get_by_text('拍摄一份作文',exact=True).wait_for()
                page.locator('.brand').click()
                page.locator('[data-action="delete-task"][data-id="'+tid+'"]').click()
                page.locator('#notice-dialog').get_by_role('button',name='确认',exact=True).click()
                page.locator('[data-action="open-task"][data-id="'+tid+'"]').wait_for(state="detached")
                assert page.request.get(f'http://127.0.0.1:{port}/api/tasks/{tid}').status==404
                phone.evaluate('checkCapture()')
                phone.get_by_role('heading',name='拍照入口已失效',exact=True).wait_for()
                page.get_by_role('button',name='账户菜单').hover()
                page.locator('.admin-account-link').click()
                page.get_by_role('button',name='阅卷追踪',exact=True).click()
                page.get_by_role('button',name='详情',exact=True).first.click()
                page.locator('.trace-call').first.locator('summary').click()
                page.get_by_text('服务商用量字段',exact=True).first.wait_for()
                page.screenshot(path=str(output/'web-admin-trace-detail.png'),full_page=True)
                assert not errors,errors
                browser.close()
                print('V3 browser workflow passed: named default, editable/deletable templates, editable classes, named directory, stable compact directory, minimal viewer, no drag/wheel movement, cursor-anchored Ctrl zoom and all close methods, transcription, image viewer, deduction/export, anonymous and mobile capture; no JavaScript errors.')
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            sock.close()


if __name__ == '__main__':
    main(Path(sys.argv[1]) if len(sys.argv)>1 else Path(tempfile.gettempdir())/'shu-browser-preview')
