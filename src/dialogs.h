#pragma once
#include "common.h"

bool InputBox(HWND owner, const std::wstring& title, const std::wstring& prompt, std::wstring& value, bool password = false);
// Filterable list picker; index refers to the original items vector.
bool PickBox(HWND owner, const std::wstring& title, const std::wstring& prompt, const std::vector<std::wstring>& items, int& index);
bool PublishBox(HWND owner, std::wstring& name, std::wstring& desc, bool& priv);
bool CloneBox(HWND owner, std::wstring& url, std::wstring& parent);
bool BrowseFolder(HWND owner, const std::wstring& title, std::wstring& out);
bool OpenFileDlg(HWND owner, std::wstring& out);
bool SaveFileDlg(HWND owner, std::wstring& inout);
