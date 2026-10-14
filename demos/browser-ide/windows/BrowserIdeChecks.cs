using System;
using System.Collections.Generic;
using System.IO;
using System.Threading.Tasks;
using Microsoft.Web.WebView2.Core;

public partial class BrowserHost {
    readonly List<CoreWebView2Frame> frames=new List<CoreWebView2Frame>();
    void TrackFrame(CoreWebView2Frame frame) {
        frames.Add(frame);
        frame.FrameCreated+=(s,e)=>TrackFrame(e.Frame);
    }
    async Task WaitJs(string expression) {
        for(int i=0;i<100;i++) {
            if(await view.CoreWebView2.ExecuteScriptAsync("Boolean("+expression+")")=="true")return;
            await Task.Delay(200);
        }
        throw new Exception("Timed out: "+expression);
    }
    async Task Key(string key,string code,int virtualKey,int modifiers=0) {
        var core=view.CoreWebView2;
        await core.CallDevToolsProtocolMethodAsync("Input.dispatchKeyEvent",json.Serialize(new{type="keyDown",key=key,code=code,windowsVirtualKeyCode=virtualKey,modifiers=modifiers,text=key=="Enter"?"\r":""}));
        await core.CallDevToolsProtocolMethodAsync("Input.dispatchKeyEvent",json.Serialize(new{type="keyUp",key=key,code=code,windowsVirtualKeyCode=virtualKey,modifiers=modifiers}));
    }
    async Task Command(string title) {
        await Key("Escape","Escape",27);
        await Key("P","KeyP",80,10);
        await WaitJs("document.querySelector('.quick-input-widget input') && document.querySelector('.quick-input-widget').offsetHeight>0");
        await view.CoreWebView2.CallDevToolsProtocolMethodAsync("Input.insertText",json.Serialize(new{text=title}));
        string row="Array.from(document.querySelectorAll('.quick-input-list .monaco-list-row')).find(e=>Array.from(e.querySelectorAll('.label-name')).some(n=>n.textContent.trim()==="+json.Serialize(title)+"))";
        await WaitJs(row);
        await view.CoreWebView2.ExecuteScriptAsync("("+row+").click()");
        await Task.Delay(500);
    }
    async Task<CoreWebView2Frame> FindFrame(string text) {
        for(int i=0;i<60;i++) {
            foreach(var frame in frames.ToArray()) {
                try {if(await frame.ExecuteScriptAsync("Boolean(document.body && document.body.innerText.includes("+json.Serialize(text)+"))")=="true")return frame;}catch{}
            }
            await Task.Delay(250);
        }
        throw new Exception("Missing frame: "+text);
    }
    async Task ProbeIde() {
        var core=view.CoreWebView2;
        string row="Array.from(document.querySelectorAll('[role=treeitem]')).find(e=>(e.getAttribute('aria-label')||'').includes('README.md'))";
        await WaitJs(row);
        await core.ExecuteScriptAsync("var file="+row+";file.click();file.dispatchEvent(new MouseEvent('dblclick',{bubbles:true}))");
        await Task.Delay(1000);
        await Command("Markdown: Open Preview");
        await FindFrame("Browser IDE boundary demo");
        Log("markdown-preview-verified");
        await Command("Terminal: Create New Terminal");
        await Task.Delay(1000);
        await Command("Terminal: Focus Terminal");
        view.Focus();
        await core.ExecuteScriptAsync("Array.from(document.querySelectorAll('.xterm-helper-textarea')).find(e=>e.offsetParent!==null).focus()");
        await WaitJs("document.activeElement.classList.contains('xterm-helper-textarea')");
        string terminalCommand="node /workspace/hello.js > /tmp/demo-windows-terminal-result";
        foreach(char c in terminalCommand) {
            int virtualKey=char.ToUpperInvariant(c);
            string code="Key"+char.ToUpperInvariant(c); int modifiers=0;
            if(c==' ') {virtualKey=32;code="Space";}
            else if(c=='/') {virtualKey=191;code="Slash";}
            else if(c=='.' || c=='>') {virtualKey=190;code="Period";if(c=='>')modifiers=8;}
            else if(c=='-') {virtualKey=189;code="Minus";}
            await core.CallDevToolsProtocolMethodAsync("Input.dispatchKeyEvent",json.Serialize(new{type="keyDown",key=c.ToString(),code=code,windowsVirtualKeyCode=virtualKey,modifiers=modifiers,text=c.ToString(),unmodifiedText=c.ToString()}));
            await core.CallDevToolsProtocolMethodAsync("Input.dispatchKeyEvent",json.Serialize(new{type="keyUp",key=c.ToString(),code=code,windowsVirtualKeyCode=virtualKey,modifiers=0}));
        }
        await Key("Enter","Enter",13);
        await Task.Delay(1000);
        Log("terminal-command-sent");
        await Command("Demo: Open Boundary Probe");
        var webview=await FindFrame("Remote extension boundary probe");
        await webview.ExecuteScriptAsync("document.querySelector('#post').click()");
        await Task.Delay(1500);
        Log("extension-webview-post-triggered");
        using(var file=File.Create(Path.Combine(root,"ide.png"))) await core.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,file);
    }
}
