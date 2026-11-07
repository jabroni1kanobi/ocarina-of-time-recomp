// Windowless launcher for playing: starts oot.exe (a console program, for its log lines) from the folder this file's
// executable is in, without a console window, and passes its own command line on. The game window shows as usual and
// the log goes to logs\oot.log beside oot.exe (runtime/main.c log_open).
//
//   clang -O2 -mwindows -municode tools/play/oot_play.c -o "build/v131072/Play Ocarina of Time.exe"
// (not OoT.exe: Windows file names ignore case, so that would replace oot.exe itself)
#include <windows.h>
#include <wchar.h>

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE prev, PWSTR args, int show) {
    (void)inst, (void)prev, (void)show;
    wchar_t dir[MAX_PATH], exe[MAX_PATH], cmd[32768];
    DWORD n = GetModuleFileNameW(NULL, dir, MAX_PATH);
    if (!n || n >= MAX_PATH) return 1;
    wchar_t *cut = wcsrchr(dir, L'\\');
    if (!cut) return 1;
    *cut = 0;
    if (_snwprintf(exe, MAX_PATH, L"%ls\\oot.exe", dir) >= MAX_PATH ||
        _snwprintf(cmd, 32768, L"\"%ls\" %ls", exe, args ? args : L"") >= 32768)
        return 1;
    if (GetFileAttributesW(exe) == INVALID_FILE_ATTRIBUTES) {
        MessageBoxW(NULL, L"oot.exe is not in this folder. Build it first (docs/building.md).", L"Ocarina of Time Recomp",
                    MB_ICONERROR);
        return 1;
    }
    STARTUPINFOW si = {.cb = sizeof si};
    PROCESS_INFORMATION pi;
    if (!CreateProcessW(exe, cmd, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, dir, &si, &pi)) {
        wchar_t msg[256];
        _snwprintf(msg, 256, L"oot.exe did not start (error %lu).", GetLastError());
        MessageBoxW(NULL, msg, L"Ocarina of Time Recomp", MB_ICONERROR);
        return 1;
    }
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 0;
}
