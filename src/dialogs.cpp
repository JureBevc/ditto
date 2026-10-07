#include "dialogs.h"
#include "resource.h"

static std::wstring GetText(HWND dlg, int id) {
    HWND h = GetDlgItem(dlg, id);
    int n = GetWindowTextLengthW(h);
    std::wstring s((size_t)n + 1, L'\0');
    GetWindowTextW(h, &s[0], n + 1);
    s.resize((size_t)n);
    return s;
}

// ---------------- input ----------------
struct InputData {
    std::wstring title, prompt, value;
    bool password;
};

static INT_PTR CALLBACK InputProc(HWND d, UINT m, WPARAM w, LPARAM l) {
    auto* p = (InputData*)GetWindowLongPtrW(d, DWLP_USER);
    switch (m) {
    case WM_INITDIALOG:
        SetWindowLongPtrW(d, DWLP_USER, l);
        p = (InputData*)l;
        SetWindowTextW(d, p->title.c_str());
        SetDlgItemTextW(d, IDC_PROMPT, p->prompt.c_str());
        SetDlgItemTextW(d, IDC_EDIT, p->value.c_str());
        if (p->password) SendDlgItemMessageW(d, IDC_EDIT, EM_SETPASSWORDCHAR, 0x25CF, 0);
        SendDlgItemMessageW(d, IDC_EDIT, EM_SETSEL, 0, -1);
        SetFocus(GetDlgItem(d, IDC_EDIT));
        return FALSE;
    case WM_COMMAND:
        if (LOWORD(w) == IDOK) { p->value = GetText(d, IDC_EDIT); EndDialog(d, IDOK); return TRUE; }
        if (LOWORD(w) == IDCANCEL) { EndDialog(d, IDCANCEL); return TRUE; }
        break;
    }
    return FALSE;
}

bool InputBox(HWND owner, const std::wstring& title, const std::wstring& prompt, std::wstring& value, bool password) {
    InputData d{title, prompt, value, password};
    if (DialogBoxParamW(g_hinst, MAKEINTRESOURCEW(IDD_INPUT), owner, InputProc, (LPARAM)&d) != IDOK) return false;
    value = d.value;
    return true;
}

// ---------------- pick ----------------
struct PickData {
    std::wstring title, prompt;
    const std::vector<std::wstring>* items;
    std::vector<int> shown;
    int result = -1;
};

static void FillPick(HWND d, PickData* p) {
    std::wstring f = LowerW(GetText(d, IDC_EDIT));
    HWND lb = GetDlgItem(d, IDC_LIST);
    SendMessageW(lb, WM_SETREDRAW, FALSE, 0);
    SendMessageW(lb, LB_RESETCONTENT, 0, 0);
    p->shown.clear();
    for (size_t i = 0; i < p->items->size(); ++i) {
        if (!f.empty() && LowerW((*p->items)[i]).find(f) == std::wstring::npos) continue;
        SendMessageW(lb, LB_ADDSTRING, 0, (LPARAM)(*p->items)[i].c_str());
        p->shown.push_back((int)i);
    }
    SendMessageW(lb, LB_SETCURSEL, 0, 0);
    SendMessageW(lb, WM_SETREDRAW, TRUE, 0);
    InvalidateRect(lb, nullptr, TRUE);
}

static LRESULT CALLBACK PickEditSub(HWND h, UINT m, WPARAM w, LPARAM l, UINT_PTR, DWORD_PTR) {
    if (m == WM_KEYDOWN && (w == VK_DOWN || w == VK_UP || w == VK_NEXT || w == VK_PRIOR)) {
        SendMessageW(GetDlgItem(GetParent(h), IDC_LIST), m, w, l);
        return 0;
    }
    return DefSubclassProc(h, m, w, l);
}

static INT_PTR CALLBACK PickProc(HWND d, UINT m, WPARAM w, LPARAM l) {
    auto* p = (PickData*)GetWindowLongPtrW(d, DWLP_USER);
    switch (m) {
    case WM_INITDIALOG:
        SetWindowLongPtrW(d, DWLP_USER, l);
        p = (PickData*)l;
        SetWindowTextW(d, p->title.c_str());
        SetDlgItemTextW(d, IDC_PROMPT, p->prompt.c_str());
        SendDlgItemMessageW(d, IDC_EDIT, EM_SETCUEBANNER, TRUE, (LPARAM)L"Type to filter");
        SetWindowSubclass(GetDlgItem(d, IDC_EDIT), PickEditSub, 1, 0);
        FillPick(d, p);
        SetFocus(GetDlgItem(d, IDC_EDIT));
        return FALSE;
    case WM_COMMAND:
        if (LOWORD(w) == IDC_EDIT && HIWORD(w) == EN_CHANGE) { FillPick(d, p); return TRUE; }
        if ((LOWORD(w) == IDC_LIST && HIWORD(w) == LBN_DBLCLK) || LOWORD(w) == IDOK) {
            int sel = (int)SendDlgItemMessageW(d, IDC_LIST, LB_GETCURSEL, 0, 0);
            if (sel >= 0 && sel < (int)p->shown.size()) {
                p->result = p->shown[(size_t)sel];
                EndDialog(d, IDOK);
            }
            return TRUE;
        }
        if (LOWORD(w) == IDCANCEL) { EndDialog(d, IDCANCEL); return TRUE; }
        break;
    }
    return FALSE;
}

bool PickBox(HWND owner, const std::wstring& title, const std::wstring& prompt, const std::vector<std::wstring>& items, int& index) {
    PickData d;
    d.title = title;
    d.prompt = prompt;
    d.items = &items;
    if (DialogBoxParamW(g_hinst, MAKEINTRESOURCEW(IDD_PICK), owner, PickProc, (LPARAM)&d) != IDOK) return false;
    index = d.result;
    return index >= 0;
}

// ---------------- publish ----------------
struct PublishData {
    std::wstring name, desc;
    bool priv;
};

static INT_PTR CALLBACK PublishProc(HWND d, UINT m, WPARAM w, LPARAM l) {
    auto* p = (PublishData*)GetWindowLongPtrW(d, DWLP_USER);
    switch (m) {
    case WM_INITDIALOG:
        SetWindowLongPtrW(d, DWLP_USER, l);
        p = (PublishData*)l;
        SetDlgItemTextW(d, IDC_EDIT, p->name.c_str());
        CheckRadioButton(d, IDC_PRIVATE, IDC_PUBLIC, p->priv ? IDC_PRIVATE : IDC_PUBLIC);
        SendDlgItemMessageW(d, IDC_EDIT, EM_SETSEL, 0, -1);
        SetFocus(GetDlgItem(d, IDC_EDIT));
        return FALSE;
    case WM_COMMAND:
        if (LOWORD(w) == IDOK) {
            p->name = Trim(GetText(d, IDC_EDIT));
            p->desc = Trim(GetText(d, IDC_EDIT2));
            p->priv = IsDlgButtonChecked(d, IDC_PRIVATE) == BST_CHECKED;
            if (p->name.empty()) { MessageBeep(MB_ICONWARNING); return TRUE; }
            EndDialog(d, IDOK);
            return TRUE;
        }
        if (LOWORD(w) == IDCANCEL) { EndDialog(d, IDCANCEL); return TRUE; }
        break;
    }
    return FALSE;
}

bool PublishBox(HWND owner, std::wstring& name, std::wstring& desc, bool& priv) {
    PublishData d{name, desc, priv};
    if (DialogBoxParamW(g_hinst, MAKEINTRESOURCEW(IDD_PUBLISH), owner, PublishProc, (LPARAM)&d) != IDOK) return false;
    name = d.name;
    desc = d.desc;
    priv = d.priv;
    return true;
}

// ---------------- clone ----------------
struct CloneData {
    std::wstring url, parent;
};

static INT_PTR CALLBACK CloneProc(HWND d, UINT m, WPARAM w, LPARAM l) {
    auto* p = (CloneData*)GetWindowLongPtrW(d, DWLP_USER);
    switch (m) {
    case WM_INITDIALOG:
        SetWindowLongPtrW(d, DWLP_USER, l);
        p = (CloneData*)l;
        SetDlgItemTextW(d, IDC_EDIT, p->url.c_str());
        SetDlgItemTextW(d, IDC_EDIT2, p->parent.c_str());
        SendDlgItemMessageW(d, IDC_EDIT, EM_SETCUEBANNER, TRUE, (LPARAM)L"https://github.com/user/repo.git");
        SetFocus(GetDlgItem(d, IDC_EDIT));
        return FALSE;
    case WM_COMMAND:
        if (LOWORD(w) == IDC_BROWSE) {
            std::wstring f = GetText(d, IDC_EDIT2);
            if (BrowseFolder(d, L"Choose parent folder", f)) SetDlgItemTextW(d, IDC_EDIT2, f.c_str());
            return TRUE;
        }
        if (LOWORD(w) == IDOK) {
            p->url = Trim(GetText(d, IDC_EDIT));
            p->parent = Trim(GetText(d, IDC_EDIT2));
            if (p->url.empty() || !IsDirectory(p->parent)) { MessageBeep(MB_ICONWARNING); return TRUE; }
            EndDialog(d, IDOK);
            return TRUE;
        }
        if (LOWORD(w) == IDCANCEL) { EndDialog(d, IDCANCEL); return TRUE; }
        break;
    }
    return FALSE;
}

bool CloneBox(HWND owner, std::wstring& url, std::wstring& parent) {
    CloneData d{url, parent};
    if (DialogBoxParamW(g_hinst, MAKEINTRESOURCEW(IDD_CLONE), owner, CloneProc, (LPARAM)&d) != IDOK) return false;
    url = d.url;
    parent = d.parent;
    return true;
}

// ---------------- shell dialogs ----------------
bool BrowseFolder(HWND owner, const std::wstring& title, std::wstring& out) {
    IFileOpenDialog* dlg = nullptr;
    if (FAILED(CoCreateInstance(CLSID_FileOpenDialog, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&dlg)))) return false;
    DWORD opts = 0;
    dlg->GetOptions(&opts);
    dlg->SetOptions(opts | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM);
    dlg->SetTitle(title.c_str());
    if (!out.empty() && IsDirectory(out)) {
        IShellItem* init = nullptr;
        if (SUCCEEDED(SHCreateItemFromParsingName(out.c_str(), nullptr, IID_PPV_ARGS(&init)))) {
            dlg->SetFolder(init);
            init->Release();
        }
    }
    bool ok = false;
    if (SUCCEEDED(dlg->Show(owner))) {
        IShellItem* item = nullptr;
        if (SUCCEEDED(dlg->GetResult(&item))) {
            PWSTR p = nullptr;
            if (SUCCEEDED(item->GetDisplayName(SIGDN_FILESYSPATH, &p))) {
                out = p;
                CoTaskMemFree(p);
                ok = true;
            }
            item->Release();
        }
    }
    dlg->Release();
    return ok;
}

static const wchar_t* kFilter = L"All Files (*.*)\0*.*\0Markdown (*.md)\0*.md;*.markdown\0Text (*.txt)\0*.txt\0";

bool OpenFileDlg(HWND owner, std::wstring& out) {
    std::vector<wchar_t> buf(32768, 0);
    OPENFILENAMEW ofn = {sizeof(ofn)};
    ofn.hwndOwner = owner;
    ofn.lpstrFilter = kFilter;
    ofn.lpstrFile = buf.data();
    ofn.nMaxFile = (DWORD)buf.size();
    ofn.Flags = OFN_EXPLORER | OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR;
    if (!GetOpenFileNameW(&ofn)) return false;
    out = buf.data();
    return true;
}

bool SaveFileDlg(HWND owner, std::wstring& inout) {
    std::vector<wchar_t> buf(32768, 0);
    wcsncpy_s(buf.data(), buf.size(), inout.c_str(), _TRUNCATE);
    OPENFILENAMEW ofn = {sizeof(ofn)};
    ofn.hwndOwner = owner;
    ofn.lpstrFilter = kFilter;
    ofn.lpstrFile = buf.data();
    ofn.nMaxFile = (DWORD)buf.size();
    ofn.Flags = OFN_EXPLORER | OFN_OVERWRITEPROMPT | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR;
    if (!GetSaveFileNameW(&ofn)) return false;
    inout = buf.data();
    return true;
}
