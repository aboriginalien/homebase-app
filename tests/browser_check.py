"""Actual Chromium + local server + synthetic provider. No live account inference.

Run with Playwright-equipped Python, passing the locked app Python and output dir.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from playwright.sync_api import sync_playwright

root=Path(__file__).resolve().parents[1]
app_python=sys.argv[1];output=Path(sys.argv[2]);output.mkdir(parents=True,exist_ok=True)
results=[]
with tempfile.TemporaryDirectory() as temp:
    private=Path(temp).resolve()
    server=subprocess.Popen([app_python,str(root/'tests/browser_server.py'),str(private)],cwd=root,
                            env={'PATH':'/usr/bin:/bin','PYTHONPATH':str(root)},stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        meta=json.loads(server.stdout.readline());origin=meta['origin']
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
            context=browser.new_context(viewport={'width':1280,'height':900})
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(origin+'/#pair='+(private/'pairing.txt').read_text())
            page.wait_for_function("() => document.querySelector('#draft').disabled === false")
            assert page.url==origin+'/'
            assert not page.evaluate('Object.keys(localStorage).length')
            assert context.cookies()[0]['httpOnly'] and context.cookies()[0]['sameSite']=='Strict'
            results.append('one-use pairing fragment removed; HttpOnly/Strict cookie; no localStorage')
            page.locator('#draft').fill('Desktop safe <script>alert(1)</script>')
            page.locator('#draft').press('Shift+Enter');assert '\n' in page.locator('#draft').input_value()
            page.locator('#draft').press('Enter')
            page.wait_for_function("() => document.querySelector('.message[data-status=completed]') !== null")
            assert page.locator('.message .body').last.text_content().startswith('Synthetic reply')
            assert not page.locator('#messages script').count()
            results.append('Enter send, Shift+Enter multiline, provider deltas rendered safely and completed')
            # Unchanged polling must not destroy selected text.
            page.evaluate("let node=document.querySelector('.message[data-status=completed] .body');let r=document.createRange();r.selectNodeContents(node);let s=window.getSelection();s.removeAllRanges();s.addRange(r)")
            selection=page.evaluate('String(window.getSelection())');page.wait_for_timeout(1600)
            assert page.evaluate('String(window.getSelection())')==selection
            results.append('completed text selection survives polling')
            page.screenshot(path=str(output/'desktop.png'),full_page=True)
            page.locator('#draft').fill('slow submitted');page.locator('#send').click()
            page.wait_for_selector('.message[data-status=working]')
            page.locator('#draft').fill('next unsent draft')
            page.wait_for_timeout(2600)
            assert page.locator('#draft').input_value()=='next unsent draft'
            page.reload();page.wait_for_function("() => document.querySelector('#draft').value === 'next unsent draft'")
            results.append('new draft survives streaming completion and reload')
            page.locator('#draft').fill('fail keep draft');page.locator('#send').click()
            page.wait_for_function("() => [...document.querySelectorAll('.message[data-status=incomplete]')].length === 1")
            assert page.locator('#draft').input_value()=='fail keep draft'
            page.locator('#send').click()
            page.wait_for_function("() => [...document.querySelectorAll('.message[data-status=incomplete]')].length === 2")
            results.append('dropped provider stream incomplete; unchanged draft can explicitly retry')
            page.locator('#draft').fill('/remember shared Travel | I prefer sleeper trains')
            page.locator('#send').click();page.wait_for_function("() => document.querySelector('#draft').value === ''")
            page.locator('#memory').click();page.wait_for_selector('.record')
            page.locator('.record textarea').fill('I prefer daytime trains');page.get_by_role('button',name='Correct',exact=True).click()
            page.wait_for_function("() => document.querySelector('.record').textContent.includes('revision 2')")
            page.get_by_role('button',name='Forget',exact=True).click();page.wait_for_function("() => document.querySelectorAll('.record').length === 0")
            page.locator('#memory').click()
            results.append('explicit conversational remember; inspect/correct/forget interface')
            page.locator('#new').click();page.wait_for_function("() => document.querySelectorAll('.message').length === 0")
            page.locator('#draft').fill('slow stop now');page.locator('#send').click();page.wait_for_selector('.message[data-status=working]')
            page.locator('#stop').click();page.wait_for_selector('.message[data-status=stopped]')
            page.wait_for_timeout(1000)
            assert page.locator('#draft').input_value()=='slow stop now'
            results.append('Stop persists incomplete status and retains draft')
            for name,width,height in [('phone',390,844),('ipad',834,1194),('keyboard-height',834,500)]:
                page.set_viewport_size({'width':width,'height':height});page.locator('#draft').focus();page.wait_for_timeout(200)
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                assert page.locator('#send').bounding_box()['height']>=44
                page.screenshot(path=str(output/(name+'.png')),full_page=True)
                results.append(name+' viewport: no horizontal overflow; touch target >=44px')
            page.locator('#threads').click();page.wait_for_selector('#thread-panel button');page.locator('#thread-panel .thread-open').last.click()
            page.wait_for_function("() => document.querySelectorAll('.message').length >= 2")
            results.append('saved thread reopened')
            page.reload();page.wait_for_function("() => document.querySelector('#draft').disabled === false")
            # Reload opens most recently updated thread, history remains in thread list.
            page.locator('#signout').click();page.wait_for_function("() => document.querySelector('#draft').disabled === true")
            assert page.locator('#messages').text_content()==''
            assert page.request.get(origin+'/api/threads').status==401
            results.append('sign-out clears UI and denies private owner routes')
            assert not errors,errors
            (output/'browser-results.json').write_text(json.dumps({'environment':'Chromium local synthetic provider, resized viewports; not real iPad or live inference','checks':results,'page_errors':errors},indent=2)+'\n')
            print(json.dumps({'checks':results,'page_errors':errors}));browser.close()
    finally:
        server.terminate();server.wait(timeout=5)
