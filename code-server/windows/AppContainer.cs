// Creates the dedicated AppContainer profile, starts a process inside it, and
// reports which AppContainer a running process belongs to.
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;

public static class BrowserAppContainer {
    // Win32 constants (winnt.h, winbase.h, winerror.h).
    const uint PROCESS_QUERY_LIMITED_INFORMATION = 0x1000;
    const uint TOKEN_QUERY = 0x0008;
    const int TokenIsAppContainer = 29;    // TOKEN_INFORMATION_CLASS
    const int TokenAppContainerSid = 31;   // TOKEN_INFORMATION_CLASS
    const uint EXTENDED_STARTUPINFO_PRESENT = 0x00080000;
    static readonly IntPtr PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = new IntPtr(0x00020009);
    // HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS): the profile was created earlier.
    const int E_ALREADY_EXISTS = unchecked((int)0x800700B7);

    [StructLayout(LayoutKind.Sequential)]
    struct SecurityCapabilities {
        public IntPtr Sid, Capabilities;
        public uint Count, Reserved;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct StartupInfo {
        public int cb;
        public string reserved, desktop, title;
        public uint x, y, cx, cy, xchars, ychars, fill, flags;
        public ushort show, reservedSize;
        public IntPtr reserved2, input, output, error;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct StartupEx {
        public StartupInfo startup;
        public IntPtr attributes;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct ProcessInfo {
        public IntPtr process, thread;
        public uint pid, tid;
    }

    [DllImport("userenv.dll", CharSet = CharSet.Unicode)]
    static extern int CreateAppContainerProfile(string name, string display, string description, IntPtr caps, uint count, out IntPtr sid);
    [DllImport("userenv.dll", CharSet = CharSet.Unicode)]
    static extern int DeriveAppContainerSidFromAppContainerName(string name, out IntPtr sid);
    [DllImport("advapi32.dll")]
    static extern IntPtr FreeSid(IntPtr sid);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool InitializeProcThreadAttributeList(IntPtr list, int count, int flags, ref IntPtr size);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool UpdateProcThreadAttribute(IntPtr list, uint flags, IntPtr attribute, IntPtr value, IntPtr size, IntPtr previous, IntPtr returned);
    [DllImport("kernel32.dll")]
    static extern void DeleteProcThreadAttributeList(IntPtr list);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool CreateProcess(string app, StringBuilder command, IntPtr processAttributes, IntPtr threadAttributes, bool inherit, uint flags, IntPtr environment, string cwd, ref StartupEx info, out ProcessInfo process);
    [DllImport("kernel32.dll")]
    static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll")]
    static extern uint WaitForSingleObject(IntPtr handle, uint timeout);
    [DllImport("kernel32.dll")]
    static extern bool GetExitCodeProcess(IntPtr handle, out uint code);
    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool GetTokenInformation(IntPtr token, int kind, IntPtr info, int size, out int needed);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);

    // Returns the AppContainer SID of process pid, or "not-appcontainer".
    public static string Inspect(uint pid) {
        IntPtr process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid);
        if (process == IntPtr.Zero) throw new Win32Exception();
        try {
            IntPtr token;
            if (!OpenProcessToken(process, TOKEN_QUERY, out token)) throw new Win32Exception();
            try {
                // The first call only reports the buffer size.
                int needed;
                GetTokenInformation(token, TokenAppContainerSid, IntPtr.Zero, 0, out needed);
                IntPtr data = Marshal.AllocHGlobal(needed);
                try {
                    if (!GetTokenInformation(token, TokenAppContainerSid, data, needed, out needed)) throw new Win32Exception();
                    // TOKEN_APPCONTAINER_INFORMATION holds one PSID.
                    IntPtr sid = Marshal.ReadIntPtr(data);
                    return sid == IntPtr.Zero ? "not-appcontainer" : new SecurityIdentifier(sid).Value;
                } finally {
                    Marshal.FreeHGlobal(data);
                }
            } finally {
                CloseHandle(token);
            }
        } finally {
            CloseHandle(process);
        }
    }

    // Creates the profile if needed and returns its SID.
    public static string Profile(string name) {
        IntPtr sid;
        int hr = CreateAppContainerProfile(name, name, "Browser IDE standard Windows isolation experiment", IntPtr.Zero, 0, out sid);
        if (hr < 0) {
            if (hr != E_ALREADY_EXISTS) Marshal.ThrowExceptionForHR(hr);
            hr = DeriveAppContainerSidFromAppContainerName(name, out sid);
            if (hr < 0) Marshal.ThrowExceptionForHR(hr);
        }
        try {
            return new SecurityIdentifier(sid).Value;
        } finally {
            FreeSid(sid);
        }
    }

    // Starts app in the AppContainer, with no capabilities, and waits up to
    // seconds for it. Returns JSON with its PID, whether its token really is an
    // AppContainer token (1, 0, or -1 if unknown), the wait result and exit code.
    public static string Launch(string name, string app, string arguments, int seconds) {
        IntPtr sid;
        int hr = DeriveAppContainerSidFromAppContainerName(name, out sid);
        if (hr < 0) Marshal.ThrowExceptionForHR(hr);

        // The first call only reports the size of a one-attribute list.
        IntPtr size = IntPtr.Zero;
        InitializeProcThreadAttributeList(IntPtr.Zero, 1, 0, ref size);
        int capabilitiesSize = Marshal.SizeOf(typeof(SecurityCapabilities));
        IntPtr list = Marshal.AllocHGlobal(size);
        IntPtr capabilities = Marshal.AllocHGlobal(capabilitiesSize);
        ProcessInfo process = new ProcessInfo();
        bool initialized = false;
        try {
            if (!InitializeProcThreadAttributeList(list, 1, 0, ref size)) throw new Win32Exception();
            initialized = true;
            Marshal.StructureToPtr(new SecurityCapabilities { Sid = sid }, capabilities, false);
            if (!UpdateProcThreadAttribute(list, 0, PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES, capabilities,
                    new IntPtr(capabilitiesSize), IntPtr.Zero, IntPtr.Zero)) {
                throw new Win32Exception();
            }

            var startup = new StartupEx();
            startup.startup.cb = Marshal.SizeOf(typeof(StartupEx));
            startup.attributes = list;
            var command = new StringBuilder("\"" + app + "\" " + arguments);
            string cwd = Environment.GetFolderPath(Environment.SpecialFolder.System);
            if (!CreateProcess(app, command, IntPtr.Zero, IntPtr.Zero, false, EXTENDED_STARTUPINFO_PRESENT,
                    IntPtr.Zero, cwd, ref startup, out process)) {
                throw new Win32Exception();
            }

            int isContainer = -1;
            IntPtr token;
            if (OpenProcessToken(process.process, TOKEN_QUERY, out token)) {
                IntPtr value = Marshal.AllocHGlobal(4);
                int needed;
                try {
                    if (GetTokenInformation(token, TokenIsAppContainer, value, 4, out needed)) isContainer = Marshal.ReadInt32(value);
                } finally {
                    Marshal.FreeHGlobal(value);
                    CloseHandle(token);
                }
            }

            uint wait = WaitForSingleObject(process.process, (uint)(seconds * 1000));
            uint code;
            GetExitCodeProcess(process.process, out code);
            return "{\"pid\":" + process.pid + ",\"tokenIsAppContainer\":" + isContainer
                + ",\"waitResult\":" + wait + ",\"exitCode\":" + code + "}";
        } finally {
            if (process.thread != IntPtr.Zero) CloseHandle(process.thread);
            if (process.process != IntPtr.Zero) CloseHandle(process.process);
            if (initialized) DeleteProcThreadAttributeList(list);
            Marshal.FreeHGlobal(list);
            Marshal.FreeHGlobal(capabilities);
            FreeSid(sid);
        }
    }
}
