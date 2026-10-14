using System;
using System.IO;
using System.Diagnostics;
class ProtocolCanary {
    static void Main(string[] args) {
        try {File.WriteAllText(args[0],"DEMO_ONLY "+BrowserAppContainer.Inspect((uint)Process.GetCurrentProcess().Id));}
        catch(UnauthorizedAccessException) {Environment.ExitCode=5;}
    }
}
